# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

from tools.system import file as file_tool


class WorkspaceFileTests(unittest.TestCase):
    def test_reads_relative_to_workspace_root_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as handle:
                handle.write("workspace contents")
            result = file_tool.read_file("README.md", project_root=root)
            self.assertTrue(result["ok"])
            self.assertEqual("README.md", result["path"])
            self.assertEqual("workspace contents", result["content"])
            escaped = file_tool.read_file(os.path.join(outside, "secret.txt"), project_root=root)
            self.assertFalse(escaped["ok"])
            self.assertIn("越界", escaped["error"])

    def test_rejects_symlinked_workspace_root(self):
        with tempfile.TemporaryDirectory() as folder:
            real = os.path.join(folder, "real")
            os.mkdir(real)
            link = os.path.join(folder, "link")
            os.symlink(real, link)
            result = file_tool.read_file("README.md", project_root=link)
            self.assertFalse(result["ok"])
            self.assertIn("无效", result["error"])

    def test_refuses_credentials_and_git_internals(self):
        with tempfile.TemporaryDirectory() as root:
            blocked = (".env", ".env.local", "server.pem", "signing.key", "id_ed25519", "app_secret.json",
                       "refresh_token.txt", ".git/config", ".git-credentials", "prod.env", ".envrc",
                       "AuthKey_123.p8", "signing.p12", "deploy_ed25519", "key.properties",
                       ".npmrc", ".aws/credentials", "service-account.json", "terraform.tfstate")
            for relative in blocked:
                path = os.path.join(root, relative)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("must never be returned")
            for relative in blocked:
                with self.subTest(path=relative):
                    result = file_tool.read_file(relative, project_root=root)
                    self.assertFalse(result["ok"])
                    self.assertIn("安全", result["error"])

    def test_sensitive_missing_and_existing_paths_have_the_same_error(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, ".env"), "w", encoding="utf-8") as handle:
                handle.write("TOP_SECRET=value")
            existing = file_tool.read_file(".env", project_root=root)
            missing = file_tool.read_file(".env.missing", project_root=root)
            self.assertEqual(existing["error"], missing["error"])

    def test_private_key_content_is_blocked_even_under_an_unusual_name(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "notes.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("-----BEGIN OPENSSH PRIVATE KEY-----\nprivate material\n")
            result = file_tool.read_file("notes.txt", project_root=root)
            self.assertFalse(result["ok"])
            self.assertIn("安全", result["error"])

    def test_common_source_files_are_not_blocked_by_partial_sensitive_names(self):
        with tempfile.TemporaryDirectory() as root:
            readable = ("tokenizer.py", "id_utils.py", "environment.md", ".gitignore",
                        ".github/workflows/ci.yml")
            for relative in readable:
                path = os.path.join(root, relative)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("safe source")
            for relative in readable:
                with self.subTest(path=relative):
                    self.assertTrue(file_tool.read_file(relative, project_root=root)["ok"])


if __name__ == "__main__":
    unittest.main()
