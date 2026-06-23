import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "svg-vectorizer"
CLAUDE_MANIFEST = PLUGIN / ".claude-plugin" / "plugin.json"
CODEX_MANIFEST = PLUGIN / ".codex-plugin" / "plugin.json"
CODEX_MCP = PLUGIN / ".mcp.json"
CLAUDE_MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
SKILL = PLUGIN / "skills" / "svg-vectorizer" / "SKILL.md"
SERVER_SCRIPT = PLUGIN / "server" / "mcp-server.cjs"


class ClaudePluginManifestTests(unittest.TestCase):
    def test_claude_manifest_exists_and_has_required_fields(self):
        manifest = json.loads(CLAUDE_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["name"], "svg-vectorizer")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(manifest["license"], "MIT")
        self.assertIn("author", manifest)
        self.assertIn("homepage", manifest)
        # Claude Code's plugin validator requires repository to be a string URL,
        # not an object. Lock that in so the manifest stays installable.
        self.assertIsInstance(manifest["repository"], str)

        description = manifest["description"]
        self.assertGreaterEqual(len(description), 50)
        self.assertLessEqual(len(description), 200)

    def test_claude_manifest_wires_shared_mcp_server_with_plugin_root(self):
        manifest = json.loads(CLAUDE_MANIFEST.read_text(encoding="utf-8"))

        servers = manifest["mcpServers"]
        self.assertIn("svgVectorizer", servers)
        server = servers["svgVectorizer"]
        self.assertEqual(server["command"], "node")
        self.assertEqual(
            server["args"],
            ["${CLAUDE_PLUGIN_ROOT}/server/mcp-server.cjs"],
        )

    def test_claude_manifest_keeps_skills_auto_discovered_not_duplicated(self):
        # Claude Code auto-discovers skills under skills/. The manifest must not
        # fork a second skills tree; the shared skills/svg-vectorizer/SKILL.md
        # is the single routing guide for both platforms.
        manifest = json.loads(CLAUDE_MANIFEST.read_text(encoding="utf-8"))
        self.assertNotIn("skills", manifest, "skills are auto-discovered, not declared")
        self.assertTrue(SKILL.exists())


class SingleCoreInvariantTests(unittest.TestCase):
    def test_server_directory_is_not_duplicated(self):
        # The core server/ must exist exactly once under the plugin and must not
        # be copied into the Claude Code shell or anywhere else.
        self.assertTrue(SERVER_SCRIPT.exists())

        duplicated = [
            path
            for path in PLUGIN.rglob("server")
            if path.is_dir() and path.parent != PLUGIN
        ]
        self.assertEqual(duplicated, [], f"server/ must not be duplicated: {duplicated}")

        self.assertFalse((PLUGIN / ".claude-plugin" / "server").exists())
        self.assertFalse((PLUGIN / "claude-server").exists())

    def test_both_shells_target_the_same_mcp_server_script(self):
        # Codex .mcp.json uses cwd-relative "./server/mcp-server.cjs"; the Claude
        # manifest uses ${CLAUDE_PLUGIN_ROOT}/server/mcp-server.cjs. Both must
        # resolve to the single shared script.
        codex_mcp = json.loads(CODEX_MCP.read_text(encoding="utf-8"))
        codex_args = codex_mcp["mcpServers"]["svgVectorizer"]["args"]
        self.assertEqual(codex_args, ["./server/mcp-server.cjs"])

        claude_manifest = json.loads(CLAUDE_MANIFEST.read_text(encoding="utf-8"))
        claude_args = claude_manifest["mcpServers"]["svgVectorizer"]["args"]
        self.assertEqual(claude_args, ["${CLAUDE_PLUGIN_ROOT}/server/mcp-server.cjs"])

        # Both reference server/mcp-server.cjs, and that file is the single core.
        self.assertTrue(codex_args[0].endswith("server/mcp-server.cjs"))
        self.assertTrue(claude_args[0].endswith("server/mcp-server.cjs"))
        self.assertTrue(SERVER_SCRIPT.exists())

    def test_both_shells_share_the_same_skill(self):
        codex_manifest = json.loads(CODEX_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(codex_manifest["skills"], "./skills/")
        self.assertTrue(SKILL.exists())

    def test_codex_manifest_remains_unchanged_shape(self):
        # The Codex shell keeps pointing at its own .mcp.json wiring; the Claude
        # shell is purely additive.
        codex_manifest = json.loads(CODEX_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(codex_manifest["mcpServers"], "./.mcp.json")
        self.assertEqual(codex_manifest["name"], "svg-vectorizer")


class ClaudeMarketplaceTests(unittest.TestCase):
    def test_marketplace_points_at_the_shared_plugin(self):
        marketplace = json.loads(CLAUDE_MARKETPLACE.read_text(encoding="utf-8"))

        self.assertEqual(marketplace["name"], "svg-tools")
        entries = {entry["name"]: entry for entry in marketplace["plugins"]}
        self.assertIn("svg-vectorizer", entries)

        entry = entries["svg-vectorizer"]
        self.assertEqual(entry["source"], "./plugins/svg-vectorizer")
        self.assertEqual(entry["category"], "productivity")

        # The marketplace source must resolve to the same plugin directory that
        # owns the .claude-plugin shell, so installation reuses the single core.
        resolved = (ROOT / entry["source"]).resolve()
        self.assertTrue((resolved / ".claude-plugin" / "plugin.json").exists())
        self.assertTrue((resolved / "server" / "mcp-server.cjs").exists())


if __name__ == "__main__":
    unittest.main()
