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


if __name__ == "__main__":
    unittest.main()
