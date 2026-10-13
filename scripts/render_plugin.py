"""Render a deployment-specific plugin into ignored build output."""

import json
import shlex
import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
config_path = root / "infra/config.env"
if not config_path.is_file():
    raise SystemExit("Copy infra/config.env.example to infra/config.env first.")
config = {}
for line in config_path.read_text().splitlines():
    if line and not line.startswith("#"):
        key, value = line.split("=", 1)
        config[key] = " ".join(shlex.split(value))
for key in (
    "GCP_PROJECT_ID",
    "GCP_PROJECT_NUMBER",
    "REGION",
    "PLUGIN_NAME",
    "PLUGIN_DISPLAY_NAME",
    "ORG_DISPLAY_NAME",
    "ADMIN_CONTACT",
    "API_SERVICE",
):
    if not config.get(key):
        raise SystemExit(f"Set {key} in infra/config.env before rendering the plugin.")

# Rebuild from source so removed files cannot survive in a later bundle.
target = root / "dist/plugin"
if target.exists():
    shutil.rmtree(target)
shutil.copytree(root / "plugin", target)
manifest_path = target / ".claude-plugin/plugin.json"
manifest = json.loads(manifest_path.read_text())
manifest.update(
    name=config["PLUGIN_NAME"],
    description=f"Publish and manage HTML pages on {config['ORG_DISPLAY_NAME']} hosting.",
    author={"name": config["ORG_DISPLAY_NAME"]},
)
manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
url = f"https://{config['API_SERVICE']}-{config['GCP_PROJECT_NUMBER']}.{config['REGION']}.run.app/mcp"
(target / ".mcp.json").write_text(
    json.dumps({"mcpServers": {"publisher": {"type": "http", "url": url}}}, indent=2)
    + "\n"
)
(target / "README.md").write_text(
    f"# {config['PLUGIN_DISPLAY_NAME']}\n\n"
    "Ask Claude to publish a complete HTML page for a client, update an existing "
    "page, add a password, or take a page offline. Connect the publisher with an "
    "allowed Google account first. The plugin sends page HTML, client names, "
    "titles and access settings to "
    f"{config['ORG_DISPLAY_NAME']}'s deployment. "
    f"Contact {config['ADMIN_CONTACT']} for access or help.\n"
)
print(target)
