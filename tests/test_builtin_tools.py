"""Testes para as ferramentas nativas do Kairos."""

import tempfile
import unittest
from pathlib import Path

from kairos_tools.builtin import (
    bash_tool,
    edit_file_tool,
    list_dir_tool,
    read_file_tool,
    register_builtin_tools,
    write_file_tool,
)
from kairos_tools.registry import ToolRegistry


class BuiltinToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_bash_tool_execution(self):
        res = await bash_tool("echo 'hello kairos'")
        self.assertTrue(res.get("success"))
        self.assertIn("hello kairos", res.get("stdout", ""))
        self.assertEqual(res.get("exit_code"), 0)

    async def test_file_operations_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = str(Path(tmpdir) / "sub" / "test.txt")

            # 1. Write
            w_res = await write_file_tool(fpath, "Linha 1\nLinha 2\nLinha 3")
            self.assertTrue(w_res.get("success"))

            # 2. Read
            r_res = await read_file_tool(fpath)
            self.assertIn("Linha 2", r_res.get("content", ""))
            self.assertEqual(r_res.get("total_lines"), 3)

            # 3. Edit
            e_res = await edit_file_tool(fpath, "Linha 2", "Linha 2 Modificada")
            self.assertTrue(e_res.get("success"))

            r_res2 = await read_file_tool(fpath)
            self.assertIn("Linha 2 Modificada", r_res2.get("content", ""))

            # 4. List dir
            l_res = await list_dir_tool(str(Path(tmpdir) / "sub"))
            self.assertEqual(l_res.get("count"), 1)
            self.assertEqual(l_res.get("entries", [])[0]["name"], "test.txt")

    async def test_registry_has_builtin_tools(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        names = reg.get_all_tool_names()
        self.assertIn("bash", names)
        self.assertIn("read_file", names)
        self.assertIn("write_file", names)
        self.assertIn("edit_file", names)
        self.assertIn("list_dir", names)
        self.assertIn("web_search", names)


if __name__ == "__main__":
    unittest.main()
