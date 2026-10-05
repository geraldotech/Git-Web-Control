"""Controle dos comandos Git definidos em AGENT.md."""

import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import threading
import tempfile

from flask import Flask, jsonify, render_template, request


BASE_DIR = Path(__file__).resolve().parent
COMMANDS = {name: [name] for name in ("status", "fetch", "pull")}


def result(code=0, stdout="", stderr=""):
    return dict(success=code == 0, code=code, stdout=stdout, stderr=stderr)


def create_app(config_path=None, *, settings=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 65536
    token = secrets.token_urlsafe(32)
    lock = threading.Lock()
    config_error = None
    path = Path(config_path or BASE_DIR / "config.json").resolve()
    def resolve_settings(values):
        if not isinstance(values, dict):
            raise ValueError("A configuração deve ser um objeto JSON.")
        repo = values.get("repoPath")
        git = values.get("gitPath") or "git"
        if not isinstance(repo, str) or not repo.strip():
            raise ValueError("Informe repoPath.")
        if not isinstance(git, str):
            raise ValueError("gitPath deve ser um texto.")
        resolved_repo = Path(repo).expanduser()
        if not resolved_repo.is_absolute():
            resolved_repo = path.parent / resolved_repo
        resolved_git = shutil.which(git)
        if not resolved_git:
            raise ValueError("Git não encontrado. Verifique gitPath ou instale o Git no PATH.")
        return resolved_repo.resolve(), resolved_git

    try:
        if settings is None:
            settings = json.loads(path.read_text(encoding="utf-8-sig"))
        repo_path, git_path = resolve_settings(settings)
    except (OSError, ValueError, TypeError) as exc:
        config_error = f"Erro de configuração: {exc}"
        repo_path = "Não configurado"
        git_path = None
    settings = dict(settings) if isinstance(settings, dict) else {}
    settings.setdefault("gitPath", "git")
    settings.setdefault("repoPath", "")
    if not isinstance(settings.get("buttons"), list):
        settings["buttons"] = []

    def run(args, *, executable=None, directory=None):
        # Não herdar variáveis capazes de redirecionar o repositório configurado.
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith("GIT_")}
        env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never",
                   GIT_EDITOR="false", GIT_SEQUENCE_EDITOR="false")
        try:
            process = subprocess.run(
                [executable or git_path, "--no-pager", *args],
                cwd=directory or repo_path, capture_output=True,
                text=True, encoding="utf-8", errors="replace", shell=False,
                timeout=120, env=env, stdin=subprocess.DEVNULL,
            )
            return result(process.returncode, process.stdout, process.stderr)
        except subprocess.TimeoutExpired:
            return result(-1, stderr="Tempo limite de 120 segundos excedido ao executar Git.")
        except OSError as exc:
            return result(-1, stderr=f"Não foi possível executar Git: {exc}")

    def validate_repo():
        if config_error:
            return result(-1, stderr=config_error)
        check = run(["rev-parse", "--is-inside-work-tree"])
        if not check["success"] or check["stdout"].strip() != "true":
            return result(-1, stderr="repoPath deve apontar para um repositório Git com árvore de trabalho.\n" + check["stderr"])
        return None

    @app.before_request
    def protect_mutations():
        if request.method == "POST" and request.path.startswith("/api/"):
            if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), token):
                return jsonify(result(-1, stderr="Recarregue a página para autorizar a operação.")), 403

    @app.after_request
    def response_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify(result(-1, stderr="Corpo da requisição muito grande.")), 413

    @app.get("/")
    def index():
        return render_template("index.html", repo_path=str(repo_path), csrf_token=token,
                               settings=settings)

    def execute(action, *, require_repo=True):
        if not lock.acquire(blocking=False):
            return jsonify(result(-1, stderr="Há outra operação Git em andamento.")), 409
        try:
            error = validate_repo() if require_repo else None
            if error:
                return jsonify(error), 503
            output = action()
            return jsonify(output), 200 if output["success"] else 400
        finally:
            lock.release()

    def persist(values):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(values, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    @app.get("/api/settings")
    def get_settings():
        with lock:
            return jsonify(dict(result(), settings=settings, resolvedRepoPath=str(repo_path)))

    @app.post("/api/settings")
    def save_settings():
        def action():
            nonlocal settings, repo_path, git_path, config_error
            body = request.get_json(silent=True)
            try:
                new_repo, new_git = resolve_settings(body)
                check = run(["rev-parse", "--is-inside-work-tree"], executable=new_git, directory=new_repo)
                if not check["success"] or check["stdout"].strip() != "true":
                    return result(-1, stderr="repoPath não é um repositório Git com árvore de trabalho.\n" + check["stderr"])
                values = {**settings, "gitPath": body.get("gitPath") or "git", "repoPath": body["repoPath"]}
                persist(values)
            except (OSError, ValueError, TypeError) as exc:
                return result(-1, stderr=f"Não foi possível salvar: {exc}")
            settings, repo_path, git_path, config_error = values, new_repo, new_git, None
            return dict(result(stdout="Configuração salva e aplicada."), settings=settings,
                        resolvedRepoPath=str(repo_path))
        return execute(action, require_repo=False)

    @app.get("/api/git/behind")
    def behind():
        def action():
            output = run(["rev-list", "--count", "HEAD..@{u}"])
            output["count"] = int(output["stdout"].strip()) if output["success"] else None
            return output
        return execute(action)

    def parse_command(command):
        if not isinstance(command, str) or not command.strip() or len(command) > 4096:
            raise ValueError("Informe um comando começando com git.")
        if any(char in command for char in ("\n", "\r", "\x00", "`")) or "$(" in command:
            raise ValueError("Use um único comando Git, sem expressões de shell.")
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        # Preserva barras de caminhos Windows. Aspas agrupam argumentos com espaços.
        lexer.escape = ""
        args = list(lexer)
        if len(args) < 2 or args[0] != "git" or args[1].startswith("-"):
            raise ValueError("Use git <subcomando> [argumentos], sem opções globais ou outro executável.")
        if any(arg and all(char in ";&|<>" for char in arg) for arg in args):
            raise ValueError("Encadeamento, pipes e redirecionamentos não são aceitos.")
        builtins = run(["--list-cmds=builtins"])
        if not builtins["success"]:
            raise ValueError(builtins["stderr"] or "Não foi possível listar os comandos Git.")
        if args[1] not in builtins["stdout"].split():
            raise ValueError("Use um subcomando nativo do Git. Aliases e programas externos não são aceitos.")
        return args[1:]

    @app.post("/api/git/console")
    def console():
        def action():
            body = request.get_json(silent=True)
            try:
                args = parse_command(body.get("command") if isinstance(body, dict) else None)
            except ValueError as exc:
                return result(-1, stderr=str(exc))
            return run(args)
        return execute(action)

    @app.post("/api/buttons")
    def save_buttons():
        def action():
            nonlocal settings
            body = request.get_json(silent=True)
            buttons = body.get("buttons") if isinstance(body, dict) else None
            if not isinstance(buttons, list) or len(buttons) > 30:
                return result(-1, stderr="Informe uma lista de até 30 botões.")
            cleaned = []
            try:
                for button in buttons:
                    if (not isinstance(button, dict) or not isinstance(button.get("label"), str)
                            or not button["label"].strip() or len(button["label"]) > 60):
                        raise ValueError("Cada botão precisa de um nome de até 60 caracteres.")
                    parse_command(button.get("command"))
                    cleaned.append({"label": button["label"].strip(), "command": button["command"].strip()})
                values = {**settings, "buttons": cleaned}
                persist(values)
            except (ValueError, OSError) as exc:
                return result(-1, stderr=f"Não foi possível salvar os botões: {exc}")
            settings = values
            return dict(result(stdout="Botões salvos."), buttons=cleaned)
        return execute(action)

    @app.get("/api/git/status")
    def status():
        return execute(lambda: run(COMMANDS["status"]))

    @app.post("/api/git/fetch")
    def fetch():
        return execute(lambda: run(COMMANDS["fetch"]))

    @app.post("/api/git/pull")
    def pull():
        return execute(lambda: run(COMMANDS["pull"]))

    @app.get("/api/git/branches")
    def branches():
        def action():
            output = run(["branch", "--format=%(refname:short)"])
            if output["success"]:
                current = run(["branch", "--show-current"])
                if not current["success"]:
                    return current
                output.update(branches=output["stdout"].splitlines(), currentBranch=current["stdout"].strip())
            return output
        return execute(action)

    @app.post("/api/git/switch")
    def switch():
        body = request.get_json(silent=True)
        branch = body.get("branch") if isinstance(body, dict) else None
        if (not isinstance(branch, str) or not branch or len(branch) > 255
                or branch.startswith("-") or branch == "HEAD"
                or any(ord(char) < 32 for char in branch)):
            return jsonify(result(-1, stderr="Informe um nome de branch válido.")), 400

        def action():
            # Validar uma referência literal evita atalhos como @{-1} e opções Git.
            check = run(["check-ref-format", "refs/heads/" + branch])
            if not check["success"]:
                return result(-1, stderr="Nome de branch inválido.")
            return run(["switch", "--", branch])
        return execute(action)

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=3333, debug=False)
