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
            blocked = (".env", ".env.local", "server.pem", "signing.key", "id_device", "app_secret.json",
                       "refresh_token.txt", ".git/config")
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


if __name__ == "__main__":
    unittest.main()
