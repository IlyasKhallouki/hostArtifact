import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
plugin = json.loads((root / "plugin/.claude-plugin/plugin.json").read_text())
market = json.loads((root / ".claude-plugin/marketplace.json").read_text())
entry = market["plugins"][0]
assert entry["name"] == plugin["name"]
assert (
    entry["version"]
    == plugin["version"]
    == re.search(r"^## (\S+)", (root / "CHANGELOG.md").read_text(), re.MULTILINE)[1]
)
# The committed plugin is a template. Rendering writes the configured bundle
# under dist/, so deployment-specific endpoints never enter tracked files.
expected = "https://publisher.example.com/mcp"
assert (
    json.loads((root / "plugin/.mcp.json").read_text())["mcpServers"]["publisher"][
        "url"
    ]
    == expected
)
assert not (root / "plugin/bin").exists()
for path in (root / "plugin").rglob("*"):
    if path.is_file():
        assert "{{" not in path.read_text(), path
assert len((root / "plugin/README.md").read_text().split()) >= 40
print("Plugin consistency passed")
