import json
import subprocess
import unittest
from pathlib import Path


SERVER = Path(__file__).resolve().parents[1] / "plugins" / "svg-vectorizer" / "server" / "mcp-server.cjs"


class McpSmokeTests(unittest.TestCase):
    def test_tools_list_contains_pipeline_tools(self):
        proc = subprocess.Popen(
            ["node", str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }
            proc.stdin.write(json.dumps(request) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            self.assertTrue(line, "server did not produce a response")
            response = json.loads(line)
            names = {tool["name"] for tool in response["result"]["tools"]}
            self.assertIn("convert_image_to_svg", names)
            self.assertIn("validate_svg_trace", names)
            self.assertIn("repair_svg_trace", names)
            self.assertIn("run_svg_pipeline", names)
        finally:
            proc.kill()
            proc.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
