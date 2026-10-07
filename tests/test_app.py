import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app import create_app


@unittest.skipUnless(shutil.which("git"), "Git necessário")
class GitAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("commit", "--allow-empty", "-m", "initial")
        self.git("branch", "feature/test")
        self.config = self.root / "config.json"
        self.app = create_app(self.config, settings={"repoPath": str(self.repo), "gitPath": "git"})
        self.client = self.app.test_client()
        page = self.client.get("/").get_data(as_text=True)
        self.headers = {"X-CSRF-Token": re.search(r'name="csrf-token" content="([^"]+)"', page)[1]}

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout.strip()

    def post(self, action, **kwargs):
        return self.client.post("/api/git/" + action, headers=self.headers, **kwargs)

    def test_status_branches_and_switch(self):
        status = self.client.get("/api/git/status")
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json["success"])
        self.assertIn("stdout", status.json)
        branches = self.client.get("/api/git/branches").json
        self.assertEqual(branches["currentBranch"], "main")
        self.assertEqual(set(branches["branches"]), {"main", "feature/test"})
        switched = self.post("switch", json={"branch": "feature/test"})
        self.assertTrue(switched.json["success"], switched.json)
        self.assertEqual(self.git("branch", "--show-current"), "feature/test")

    def test_direct_commit_adds_commits_and_pushes(self):
        remote = self.root / "remote.git"
        self.git("init", "--bare", str(remote))
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-u", "origin", "main")
        (self.repo / "new.txt").write_text("new")
        message = 'Corrige cadastro "nome"; $(echo literal)'
        response = self.post("direct-commit", json={"message": message})
        self.assertTrue(response.json["success"], response.json)
        self.assertEqual(self.git("log", "-1", "--format=%s"), message)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("rev-parse", "main", cwd=remote))
        self.assertEqual(self.git("show", "main:new.txt", cwd=remote), "new")

    def test_direct_commit_validation_and_stops_on_failure(self):
        self.assertEqual(self.client.post("/api/git/direct-commit").status_code, 403)
        for body in ({}, [], {"message": " "}, {"message": 123},
                     {"message": "x" * 4097}, {"message": "bad\x00message"}):
            with patch("app.subprocess.run", wraps=subprocess.run) as run:
                response = self.post("direct-commit", json=body)
                self.assertFalse(response.json["success"])
                self.assertEqual(run.call_count, 1)  # Repository validation only.
        with patch("app.subprocess.run", wraps=subprocess.run) as run:
            response = self.post("direct-commit", json={"message": "No changes"})
            self.assertFalse(response.json["success"])
            self.assertFalse(any("push" in call.args[0] for call in run.call_args_list))
        (self.repo / "local.txt").write_text("local")
        response = self.post("direct-commit", json={"message": "Local commit"})
        self.assertFalse(response.json["success"])  # No remote configured.
        self.assertIn("$ git push", response.json["stdout"])
        self.assertEqual(self.git("log", "-1", "--format=%s"), "Local commit")

    def test_rejects_invalid_branches_and_requests(self):
        for branch in ("--discard-changes", "-", "@{-1}", "a b", "a\nb", "HEAD", "", None, 123):
            with self.subTest(branch=branch):
                self.assertEqual(self.post("switch", json={"branch": branch}).status_code, 400)
        self.assertEqual(self.post("switch", json=[]).status_code, 400)
        self.assertEqual(self.post("switch", data="{").status_code, 400)
        self.assertEqual(self.client.post("/api/git/fetch").status_code, 403)
        self.assertEqual(self.client.get("/api/exec?cmd=status").status_code, 404)
        self.assertEqual(self.git("branch", "--show-current"), "main")

    def test_git_failure_preserves_changes(self):
        (self.repo / "file.txt").write_text("main")
        self.git("add", "file.txt")
        self.git("commit", "-m", "file")
        (self.repo / "file.txt").write_text("unsaved")
        response = self.post("switch", json={"branch": "feature/test"})
        self.assertFalse(response.json["success"])
        self.assertTrue(response.json["stderr"])
        self.assertEqual((self.repo / "file.txt").read_text(), "unsaved")

    def test_fetch_pull_and_remote_switch(self):
        remote = self.root / "remote.git"
        self.git("init", "--bare", str(remote))
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-u", "origin", "main")
        other = self.root / "other"
        self.git("clone", "-b", "main", str(remote), str(other))
        self.git("config", "user.name", "Test", cwd=other)
        self.git("config", "user.email", "test@example.invalid", cwd=other)
        (other / "remote.txt").write_text("from remote")
        self.git("add", ".", cwd=other)
        self.git("commit", "-m", "remote update", cwd=other)
        self.git("push", cwd=other)
        self.git("branch", "remote-feature", cwd=other)
        self.git("push", "origin", "remote-feature", cwd=other)
        self.assertTrue(self.post("fetch").json["success"])
        branches = self.client.get("/api/git/branches").json
        self.assertEqual(branches["branches"], ["feature/test", "main"])
        # Branches remotas entram pelo nome curto: `git switch origin/x` é recusado pelo Git,
        # mas `git switch x` cria a branch local a partir da remota (origin/main fica de fora
        # porque já existe localmente).
        self.assertEqual(branches["remoteBranches"], ["remote-feature"])
        self.assertEqual(self.client.get("/api/git/behind").json["count"], 1)
        self.assertFalse((self.repo / "remote.txt").exists())
        self.assertTrue(self.post("pull").json["success"])
        self.assertEqual(self.client.get("/api/git/behind").json["count"], 0)
        self.assertEqual((self.repo / "remote.txt").read_text(), "from remote")
        # A busca automática do painel atualiza as referências sem clique em Fetch.
        (other / "auto.txt").write_text("auto")
        self.git("add", ".", cwd=other)
        self.git("commit", "-m", "commit novo", cwd=other)
        self.git("push", cwd=other)
        self.assertEqual(self.client.get("/api/git/behind").json["count"], 0)
        self.assertTrue(self.post("autofetch").json["success"])
        self.assertEqual(self.client.get("/api/git/behind").json["count"], 1)
        # Troca criando a branch local a partir da remota, como a lista da interface oferece.
        self.assertTrue(self.post("switch", json={"branch": "remote-feature"}).json["success"])
        self.assertEqual(self.git("branch", "--show-current"), "remote-feature")
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "remote-feature@{u}"), "origin/remote-feature")

    def test_invalid_configuration(self):
        for settings in ({"repoPath": str(self.root)},
                         {"repoPath": str(self.repo), "gitPath": "missing-git-executable"},
                         {"repoPath": ""}):
            client = create_app(settings=settings).test_client()
            response = client.get("/api/git/status")
            self.assertEqual(response.status_code, 503)
            self.assertFalse(response.json["success"])
            self.assertEqual(client.get("/").status_code, 200)

    def test_json_config_relative_path_and_missing_file(self):
        config = self.root / "config.json"
        config.write_text(json.dumps({"repoPath": "repo"}), encoding="utf-8")
        self.assertTrue(create_app(config).test_client().get("/api/git/status").json["success"])
        config.write_text("broken", encoding="utf-8")
        self.assertEqual(create_app(config).test_client().get("/api/git/status").status_code, 503)
        self.assertEqual(create_app(self.root / "absent.json").test_client().get("/api/git/status").status_code, 503)

    def test_timeout_and_process_error(self):
        for error in (subprocess.TimeoutExpired("git", 120), OSError("unavailable")):
            with patch("app.subprocess.run", side_effect=error):
                response = self.client.get("/api/git/status")
                self.assertFalse(response.json["success"])
                self.assertTrue(response.json["stderr"])

    def test_behind_without_upstream(self):
        response = self.client.get("/api/git/behind")
        self.assertFalse(response.json["success"])
        self.assertIsNone(response.json["count"])
        self.assertTrue(response.json["stderr"])

    def test_autofetch_without_remote_is_a_noop(self):
        # `git fetch` sem remoto sai com código 0 (nada a buscar), então não vira erro no painel.
        response = self.post("autofetch")
        self.assertTrue(response.json["success"], response.json)
        self.assertIsNone(self.client.get("/api/git/behind").json["count"])

    def test_settings_save_apply_and_reload(self):
        other = self.root / "another repo"
        other.mkdir()
        self.git("init", "-b", "other", cwd=other)
        response = self.client.post("/api/settings", headers=self.headers,
                                    json={"gitPath": shutil.which("git"), "repoPath": str(other)})
        self.assertTrue(response.json["success"], response.json)
        self.assertEqual(self.client.get("/api/git/branches").json["currentBranch"], "other")
        saved = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(saved["projects"][0]["repoPath"], str(other))
        restarted = create_app(self.config).test_client()
        self.assertEqual(restarted.get("/api/git/branches").json["currentBranch"], "other")
        self.assertIn(str(other), restarted.get("/").get_data(as_text=True))

    def test_invalid_settings_and_failed_save_preserve_active_repo(self):
        for body in ([], {}, {"gitPath": "no-such-git", "repoPath": str(self.repo)},
                     {"gitPath": "git", "repoPath": str(self.root)}):
            response = self.client.post("/api/settings", headers=self.headers, json=body)
            self.assertFalse(response.json["success"])
            self.assertEqual(self.client.get("/api/git/branches").json["currentBranch"], "main")
        with patch("app.os.replace", side_effect=PermissionError("read-only")):
            response = self.client.post("/api/settings", headers=self.headers,
                                        json={"gitPath": "git", "repoPath": str(self.repo)})
            self.assertFalse(response.json["success"])
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_repair_missing_configuration_from_panel(self):
        client = create_app(self.config).test_client()
        page = client.get("/").get_data(as_text=True)
        headers = {"X-CSRF-Token": re.search(r'name="csrf-token" content="([^"]+)"', page)[1]}
        response = client.post("/api/settings", headers=headers,
                               json={"gitPath": "git", "repoPath": str(self.repo)})
        self.assertTrue(response.json["success"])
        self.assertTrue(client.get("/api/git/status").json["success"])

    def test_console_runs_git_with_quoted_arguments(self):
        response = self.post("console", json={"command": 'git commit --allow-empty -m "Mensagem com espaços"'})
        self.assertTrue(response.json["success"], response.json)
        self.assertEqual(self.git("log", "-1", "--format=%s"), "Mensagem com espaços")
        self.assertTrue(self.post("console", json={"command": "git log --oneline -2"}).json["success"])

    def test_console_rejects_shell_aliases_and_other_executables(self):
        self.git("config", "alias.custom", "!echo forbidden")
        for command in ("python app.py", "git status && whoami", "git status;whoami",
                        "git status | more", "git status > out.txt", "git status\nwhoami",
                        "git custom", "git -c alias.custom=!whoami custom", "git", "git status $(whoami)",
                        'git log "unterminated', None):
            with self.subTest(command=command):
                response = self.post("console", json={"command": command})
                self.assertFalse(response.json["success"], response.json)
        self.assertFalse((self.repo / "out.txt").exists())

    def test_custom_buttons_persist_edit_delete_and_validate(self):
        buttons = [{"label": "Log", "command": "git log --oneline -5"}]
        response = self.client.post("/api/buttons", headers=self.headers, json={"buttons": buttons})
        self.assertTrue(response.json["success"], response.json)
        restarted = create_app(self.config).test_client()
        self.assertEqual(restarted.get("/api/settings").json["settings"]["buttons"], buttons)
        buttons[0]["label"] = "Histórico"
        self.assertTrue(self.client.post("/api/buttons", headers=self.headers, json={"buttons": buttons}).json["success"])
        invalid = [{"label": "Shell", "command": "cmd /c whoami"}]
        self.assertFalse(self.client.post("/api/buttons", headers=self.headers, json={"buttons": invalid}).json["success"])
        self.assertEqual(self.client.get("/api/settings").json["settings"]["buttons"], buttons)
        self.assertTrue(self.client.post("/api/buttons", headers=self.headers, json={"buttons": []}).json["success"])
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8"))["projects"][0]["buttons"], [])

    def add_project(self, name="Segundo projeto"):
        other = self.root / "second repo"
        other.mkdir(exist_ok=True)
        self.git("init", "-b", "second", cwd=other)
        response = self.client.post("/api/projects", headers=self.headers, json={
            "name": name, "gitPath": shutil.which("git"), "repoPath": str(other)})
        self.assertTrue(response.json["success"], response.json)
        return response.json["settings"], other

    def test_projects_isolate_commands_settings_buttons_and_reload(self):
        first = self.client.get("/api/settings").json["settings"]
        buttons = [{"label": "Status", "command": "git status"}]
        self.client.post("/api/buttons", headers=self.headers, json={"buttons": buttons})
        second, other = self.add_project()
        query = "?projectId=" + second["id"]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["buttons"], [])
        self.assertEqual(self.client.get("/api/git/branches" + query).json["currentBranch"], "second")
        self.assertEqual(self.client.get("/api/git/branches").json["currentBranch"], "main")
        response = self.client.post("/api/git/console" + query, headers=self.headers,
                                    json={"command": "git config project.marker second"})
        self.assertTrue(response.json["success"], response.json)
        self.assertEqual(self.git("config", "project.marker", cwd=other), "second")
        self.assertNotIn("project.marker", self.git("config", "--local", "--list"))
        edited = {"name": "Renomeado", "gitPath": "git", "repoPath": str(other)}
        self.assertTrue(self.client.post("/api/settings" + query, headers=self.headers, json=edited).json["success"])
        shortcuts = [{"label": "Branches", "command": "git branch"}]
        self.assertTrue(self.client.post("/api/buttons" + query, headers=self.headers,
                                        json={"buttons": shortcuts}).json["success"])
        restarted = create_app(self.config).test_client()
        self.assertEqual(len(restarted.get("/api/projects").json["projects"]), 2)
        self.assertEqual(restarted.get("/api/settings").json["settings"]["buttons"], buttons)
        self.assertEqual(restarted.get("/api/settings").json["settings"]["repoPath"], str(self.repo))
        saved = restarted.get("/api/settings" + query).json["settings"]
        self.assertEqual(saved["name"], "Renomeado")
        self.assertEqual(saved["buttons"], shortcuts)
        self.assertIn("Renomeado", restarted.get("/" + query).get_data(as_text=True))

    def test_project_creation_validation_and_failed_write(self):
        valid = {"name": "New", "gitPath": "git", "repoPath": str(self.repo)}
        for body in ([], {}, {**valid, "name": " "}, {**valid, "name": "x" * 81},
                     {**valid, "repoPath": str(self.root)}, {**valid, "gitPath": "missing-git"}):
            response = self.client.post("/api/projects", headers=self.headers, json=body)
            self.assertEqual(response.status_code, 400, response.json)
        with patch("app.os.replace", side_effect=PermissionError("read-only")):
            self.assertFalse(self.client.post("/api/projects", headers=self.headers, json=valid).json["success"])
        self.assertEqual(len(self.client.get("/api/projects").json["projects"]), 1)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_unknown_project_never_falls_back_to_another_repository(self):
        for endpoint in ("settings", "git/status", "git/branches", "git/behind"):
            self.assertEqual(self.client.get("/api/" + endpoint + "?projectId=missing").status_code, 404)
        for endpoint in ("settings", "buttons", "git/console", "git/fetch", "git/pull", "git/switch"):
            self.assertEqual(self.client.post("/api/" + endpoint + "?projectId=missing",
                                             headers=self.headers, json={"branch": "main"}).status_code, 404)

    def test_legacy_configuration_migrates_without_losing_buttons(self):
        legacy = {"gitPath": "git", "repoPath": "repo",
                  "buttons": [{"label": "Log", "command": "git log -1"}]}
        self.config.write_text(json.dumps(legacy), encoding="utf-8")
        client = create_app(self.config).test_client()
        page = client.get("/").get_data(as_text=True)
        headers = {"X-CSRF-Token": re.search(r'name="csrf-token" content="([^"]+)"', page)[1]}
        self.assertTrue(client.get("/api/git/status").json["success"])
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8")), legacy)
        self.assertTrue(client.post("/api/settings", headers=headers, json={**legacy, "name": "Existing"}).json["success"])
        saved = json.loads(self.config.read_text(encoding="utf-8"))["projects"][0]
        self.assertEqual(saved["buttons"], legacy["buttons"])
        self.assertEqual(saved["name"], "Existing")

    def test_invalid_project_does_not_block_healthy_project(self):
        second, _ = self.add_project()
        saved = json.loads(self.config.read_text(encoding="utf-8"))
        saved["projects"][0]["gitPath"] = "missing-git"
        client = create_app(self.config, settings=saved).test_client()
        self.assertEqual(client.get("/api/git/status").status_code, 503)
        self.assertTrue(client.get("/api/git/status?projectId=" + second["id"]).json["success"])

    def test_new_mutations_require_token(self):
        for endpoint in ("settings", "buttons", "git/console", "projects", "projects/delete"):
            self.assertEqual(self.client.post("/api/" + endpoint, json={}).status_code, 403)

    def test_delete_project_preserves_repositories_and_other_projects(self):
        second, other = self.add_project()
        query = "?projectId=" + second["id"]
        marker = other / "keep.txt"
        marker.write_text("keep my files", encoding="utf-8")
        self.client.post("/api/buttons" + query, headers=self.headers,
                         json={"buttons": [{"label": "Status", "command": "git status"}]})
        first = self.client.get("/api/settings").json["settings"]
        response = self.client.post("/api/projects/delete" + query, headers=self.headers, json={})
        self.assertTrue(response.json["success"], response.json)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep my files")
        self.assertEqual(self.git("branch", "--show-current", cwd=other), "second")
        self.assertEqual(self.client.get("/api/settings").json["settings"], first)
        restarted = create_app(self.config).test_client()
        self.assertEqual(restarted.get("/api/projects").json["projects"],
                         [{"id": first["id"], "name": first["name"]}])
        self.assertEqual(restarted.get("/api/git/status" + query).status_code, 404)
        self.assertEqual(restarted.get("/" + query).status_code, 200)

    def test_delete_last_project_persists_empty_state_and_can_add_again(self):
        project = self.client.get("/api/settings").json["settings"]
        response = self.client.post("/api/projects/delete?projectId=" + project["id"],
                                    headers=self.headers, json={})
        self.assertTrue(response.json["success"], response.json)
        self.assertEqual(self.client.get("/api/projects").json["projects"], [])
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8")), {"projects": []})
        self.assertEqual(self.git("branch", "--show-current"), "main")
        restarted = create_app(self.config).test_client()
        self.assertEqual(restarted.get("/api/projects").json["projects"], [])
        page = restarted.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(restarted.get("/api/git/status").status_code, 404)
        headers = {"X-CSRF-Token": re.search(r'name="csrf-token" content="([^"]+)"', page.get_data(as_text=True))[1]}
        added = restarted.post("/api/projects", headers=headers, json={
            "name": "Back", "gitPath": "git", "repoPath": str(self.repo)})
        self.assertTrue(added.json["success"], added.json)
        self.assertEqual(len(restarted.get("/api/projects").json["projects"]), 1)
        self.assertTrue(restarted.get("/api/git/status").json["success"])

    def test_delete_requires_explicit_known_id_and_preserves_state_on_write_failure(self):
        second, _ = self.add_project()
        saved = self.config.read_text(encoding="utf-8")
        for query, status in (("", 400), ("?projectId=", 400), ("?projectId=missing", 404)):
            response = self.client.post("/api/projects/delete" + query, headers=self.headers, json={})
            self.assertEqual(response.status_code, status)
        with patch("app.os.replace", side_effect=PermissionError("read-only")):
            response = self.client.post("/api/projects/delete?projectId=" + second["id"],
                                        headers=self.headers, json={})
            self.assertFalse(response.json["success"])
        self.assertEqual(self.config.read_text(encoding="utf-8"), saved)
        self.assertEqual(len(self.client.get("/api/projects").json["projects"]), 2)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_delete_invalid_repository_does_not_execute_git(self):
        client = create_app(self.config, settings={"projects": [
            {"id": "broken", "name": "Broken", "gitPath": "missing-git", "repoPath": "missing"}
        ]}).test_client()
        page = client.get("/").get_data(as_text=True)
        headers = {"X-CSRF-Token": re.search(r'name="csrf-token" content="([^"]+)"', page)[1]}
        with patch("app.subprocess.run") as run:
            response = client.post("/api/projects/delete?projectId=broken", headers=headers, json={})
            self.assertTrue(response.json["success"], response.json)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
