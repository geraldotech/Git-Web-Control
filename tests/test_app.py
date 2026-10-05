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
        self.assertEqual(self.client.get("/api/git/behind").json["count"], 1)
        self.assertFalse((self.repo / "remote.txt").exists())
        self.assertTrue(self.post("pull").json["success"])
        self.assertEqual(self.client.get("/api/git/behind").json["count"], 0)
        self.assertEqual((self.repo / "remote.txt").read_text(), "from remote")
        self.assertTrue(self.post("switch", json={"branch": "remote-feature"}).json["success"])

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

    def test_settings_save_apply_and_reload(self):
        other = self.root / "another repo"
        other.mkdir()
        self.git("init", "-b", "other", cwd=other)
        response = self.client.post("/api/settings", headers=self.headers,
                                    json={"gitPath": shutil.which("git"), "repoPath": str(other)})
        self.assertTrue(response.json["success"], response.json)
        self.assertEqual(self.client.get("/api/git/branches").json["currentBranch"], "other")
        saved = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(saved["repoPath"], str(other))
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
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8"))["buttons"], [])

    def test_new_mutations_require_token(self):
        for endpoint in ("settings", "buttons", "git/console"):
            self.assertEqual(self.client.post("/api/" + endpoint, json={}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
