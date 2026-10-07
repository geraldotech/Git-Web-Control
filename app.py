"""Controle dos comandos Git definidos em AGENT.md."""

import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import tempfile

from flask import Flask, g, jsonify, render_template, request


BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BASE_DIR
COMMANDS = {name: [name] for name in ("status", "fetch", "pull")}


def result(code=0, stdout="", stderr=""):
    return dict(success=code == 0, code=code, stdout=stdout, stderr=stderr)


def create_app(config_path=None, *, settings=None):
    app = Flask(__name__, template_folder=str(BASE_DIR / "templates"),
                static_folder=str(BASE_DIR / "static"))
    app.config["MAX_CONTENT_LENGTH"] = 65536
    # Sem debug o Jinja guarda o template em memória; recarregar evita servir HTML antigo após edições.
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    token = secrets.token_urlsafe(32)
    lock = threading.Lock()
    config_error = None
    path = Path(config_path or CONFIG_DIR / "config.json").resolve()
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
    except (OSError, ValueError, TypeError) as exc:
        config_error = f"Erro de configuração: {exc}"
    settings = dict(settings) if isinstance(settings, dict) else {}
    projects = settings.get("projects")
    if not isinstance(projects, list):
        projects = [{**settings, "id": "default", "name": settings.get("name") or "Meu projeto"}]
    projects = [dict(project) for project in projects if isinstance(project, dict)]
    used_ids = set()
    for project in projects:
        if not isinstance(project.get("id"), str) or not project["id"] or project["id"] in used_ids:
            project["id"] = secrets.token_hex(16)
        used_ids.add(project["id"])
        project.setdefault("name", "Meu projeto")
        project.setdefault("gitPath", "git")
        project.setdefault("repoPath", "")
        if not isinstance(project.get("buttons"), list):
            project["buttons"] = []

    def select_project():
        project_id = request.args.get("projectId", projects[0]["id"] if projects else None)
        g.project = next((project for project in projects if project["id"] == project_id), None)
        if g.project is None:
            return jsonify(result(-1, stderr="Projeto não encontrado.")), 404
        g.repo_path, g.git_path, g.config_error = "Não configurado", None, config_error
        try:
            g.repo_path, g.git_path = resolve_settings(g.project)
            g.config_error = None
        except (OSError, ValueError, TypeError) as exc:
            g.config_error = f"Erro de configuração: {exc}"

    def project_name(body, fallback=None):
        name = body.get("name", fallback)
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("Informe um nome de projeto de até 80 caracteres.")
        return name.strip()

    def persist_project(values, *, new=False):
        updated = [*projects, values] if new else [
            values if project["id"] == values["id"] else project for project in projects]
        persist({"projects": updated})
        projects[:] = updated

    def run(args, *, executable=None, directory=None, timeout=120):
        # Não herdar variáveis capazes de redirecionar o repositório configurado.
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith("GIT_")}
        env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never",
                   GIT_EDITOR="false", GIT_SEQUENCE_EDITOR="false")
        try:
            process = subprocess.run(
                # A saída vai por pipe, e aí o Git desligaria a cor sozinho: forçamos como se
                # fosse um terminal. O painel converte os códigos ANSI em elementos coloridos.
                [executable or g.git_path, "-c", "color.ui=always", "--no-pager", *args],
                cwd=directory or g.repo_path, capture_output=True,
                text=True, encoding="utf-8", errors="replace", shell=False,
                timeout=timeout, env=env, stdin=subprocess.DEVNULL,
            )
            return result(process.returncode, process.stdout, process.stderr)
        except subprocess.TimeoutExpired:
            return result(-1, stderr=f"Tempo limite de {timeout} segundos excedido ao executar Git.")
        except OSError as exc:
            return result(-1, stderr=f"Não foi possível executar Git: {exc}")

    def validate_repo():
        if g.config_error:
            return result(-1, stderr=g.config_error)
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
        with lock:
            error = select_project()
            if error:
                # Keep the panel available after the last project is removed or a saved URL expires.
                return render_template("index.html", repo_path="Nenhum projeto selecionado",
                                       csrf_token=token, settings={})
            return render_template("index.html", repo_path=str(g.repo_path), csrf_token=token,
                                   settings=g.project)

    def execute(action, *, require_repo=True, require_project=True):
        if not lock.acquire(blocking=False):
            return jsonify(result(-1, stderr="Há outra operação Git em andamento.")), 409
        try:
            if require_project:
                selection_error = select_project()
                if selection_error:
                    return selection_error
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
            error = select_project()
            if error:
                return error
            return jsonify(dict(result(), settings=g.project, resolvedRepoPath=str(g.repo_path)))

    @app.get("/api/projects")
    def get_projects():
        with lock:
            return jsonify(dict(result(), projects=[{"id": p["id"], "name": p["name"]} for p in projects]))

    @app.post("/api/projects")
    def add_project():
        def action():
            body = request.get_json(silent=True)
            try:
                new_repo, new_git = resolve_settings(body)
                name = project_name(body)
                check = run(["rev-parse", "--is-inside-work-tree"], executable=new_git, directory=new_repo)
                if not check["success"] or check["stdout"].strip() != "true":
                    return result(-1, stderr="repoPath não é um repositório Git com árvore de trabalho.\n" + check["stderr"])
                values = {"id": secrets.token_hex(16), "name": name,
                          "gitPath": body.get("gitPath") or "git", "repoPath": body["repoPath"], "buttons": []}
                persist_project(values, new=True)
            except (OSError, ValueError, TypeError) as exc:
                return result(-1, stderr=f"Não foi possível adicionar: {exc}")
            return dict(result(stdout="Projeto adicionado."), settings=values, resolvedRepoPath=str(new_repo))
        return execute(action, require_repo=False, require_project=False)

    @app.post("/api/projects/delete")
    def delete_project():
        if not request.args.get("projectId"):
            return jsonify(result(-1, stderr="Informe o projeto a excluir.")), 400

        def action():
            updated = [project for project in projects if project["id"] != g.project["id"]]
            try:
                persist({"projects": updated})
            except OSError as exc:
                return result(-1, stderr=f"Não foi possível excluir: {exc}")
            projects[:] = updated
            return result(stdout="Projeto excluído do painel. Os arquivos do repositório foram mantidos.")
        return execute(action, require_repo=False)

    @app.post("/api/settings")
    def save_settings():
        def action():
            body = request.get_json(silent=True)
            try:
                new_repo, new_git = resolve_settings(body)
                check = run(["rev-parse", "--is-inside-work-tree"], executable=new_git, directory=new_repo)
                if not check["success"] or check["stdout"].strip() != "true":
                    return result(-1, stderr="repoPath não é um repositório Git com árvore de trabalho.\n" + check["stderr"])
                values = {**g.project, "name": project_name(body, g.project["name"]),
                          "gitPath": body.get("gitPath") or "git", "repoPath": body["repoPath"]}
                persist_project(values)
            except (OSError, ValueError, TypeError) as exc:
                return result(-1, stderr=f"Não foi possível salvar: {exc}")
            return dict(result(stdout="Configuração salva e aplicada."), settings=values,
                        resolvedRepoPath=str(new_repo))
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
                values = {**g.project, "buttons": cleaned}
                persist_project(values)
            except (ValueError, OSError) as exc:
                return result(-1, stderr=f"Não foi possível salvar os botões: {exc}")
            return dict(result(stdout="Botões salvos."), buttons=cleaned)
        return execute(action)

    @app.get("/api/git/status")
    def status():
        return execute(lambda: run(COMMANDS["status"]))

    @app.get("/api/git/changes")
    def changes():
        def action():
            output = run(["status", "--porcelain", "--untracked-files=normal"])
            output["hasChanges"] = bool(output["stdout"].strip()) if output["success"] else None
            return output
        return execute(action)

    @app.post("/api/git/fetch")
    def fetch():
        return execute(lambda: run(COMMANDS["fetch"]))

    @app.post("/api/git/autofetch")
    def autofetch():
        # Usado pelo painel ao abrir a página: prazo curto para não segurar o lock do Git
        # quando o remoto (VPN) está fora; a falha é exibida, não interrompe o carregamento.
        return execute(lambda: run(COMMANDS["fetch"], timeout=30))

    @app.post("/api/git/pull")
    def pull():
        return execute(lambda: run(COMMANDS["pull"]))

    @app.post("/api/git/direct-commit")
    def direct_commit():
        def action():
            body = request.get_json(silent=True)
            message = body.get("message") if isinstance(body, dict) else None
            if (not isinstance(message, str) or not message.strip()
                    or len(message) > 4096 or "\x00" in message):
                return result(-1, stderr="Informe uma mensagem de commit de até 4096 caracteres.")
            stdout, stderr = [], []
            for args in (["add", "."], ["commit", "-m", message.strip()], ["push"]):
                output = run(args)
                stdout.append(f"$ git {args[0]}\n" + output["stdout"])
                stderr.append(output["stderr"])
                if not output["success"]:
                    break
            return result(output["code"], "\n".join(stdout), "\n".join(filter(None, stderr)))
        return execute(action)

    @app.get("/api/git/branches")
    def branches():
        def action():
            output = run(["branch", "--format=%(refname:short)"])
            if output["success"]:
                current = run(["branch", "--show-current"])
                if not current["success"]:
                    return current
                local = [name for name in output["stdout"].splitlines() if name]
                # `git switch nome` cria a branch local a partir da remota (DWIM), mas recusa o
                # nome qualificado (origin/x). Só entram nomes sem equivalente local e presentes
                # em um único remoto, para não oferecer uma opção ambígua.
                remotes = run(["branch", "-r", "--format=%(refname:short)"])
                counts = {}
                for name in (remotes["stdout"].splitlines() if remotes["success"] else []):
                    if not name or name.endswith("/HEAD"):
                        continue
                    short = name.split("/", 1)[-1]
                    if short not in local:
                        counts[short] = counts.get(short, 0) + 1
                remote = sorted(short for short, count in counts.items() if count == 1)
                output.update(branches=local, remoteBranches=remote, currentBranch=current["stdout"].strip())
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
