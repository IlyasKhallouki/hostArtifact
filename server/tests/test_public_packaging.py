import json
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile


def test_packaging_keeps_deployment_details_out_of_source(tmp_path):
    source = Path(__file__).resolve().parents[2]
    shutil.copytree(source / "plugin", tmp_path / "plugin")
    (tmp_path / "scripts").mkdir()
    for name in ("render_plugin.py", "package_plugin.py"):
        shutil.copy2(source / "scripts" / name, tmp_path / "scripts" / name)
    template = {
        p.relative_to(tmp_path): p.read_bytes()
        for p in (tmp_path / "plugin").rglob("*")
        if p.is_file()
    }
    command = [sys.executable, str(tmp_path / "scripts/package_plugin.py")]
    missing = subprocess.run(command, capture_output=True, text=True)
    assert missing.returncode != 0
    assert "config.env.example" in missing.stderr

    (tmp_path / "infra").mkdir()
    config = (source / "infra/config.env.example").read_text()
    config = config.replace("GCP_PROJECT_ID=\n", "GCP_PROJECT_ID=test-project\n")
    config = config.replace(
        "GCP_PROJECT_NUMBER=\n", "GCP_PROJECT_NUMBER=123456789012\n"
    )
    config = config.replace("admin@example.com", "operator@example.org")
    (tmp_path / "infra/config.env").write_text(config)
    subprocess.run(command, check=True, capture_output=True, text=True)
    archive_path = next((tmp_path / "dist").glob("*.zip"))
    with ZipFile(archive_path) as archive:
        metadata = json.loads(archive.read(".mcp.json"))
        assert metadata["mcpServers"]["publisher"]["url"] == (
            "https://artifact-publisher-api-123456789012.europe-west1.run.app/mcp"
        )
        assert "operator@example.org" in archive.read("README.md").decode()
        assert ".claude-plugin/plugin.json" in archive.namelist()
        assert not any("config.env" in name for name in archive.namelist())
    assert all(
        (tmp_path / path).read_bytes() == data for path, data in template.items()
    )

    # Rebuilding must not keep files removed from the source template.
    stale = tmp_path / "dist/plugin/old.txt"
    stale.write_text("stale deployment content")
    subprocess.run(command, check=True, capture_output=True, text=True)
    with ZipFile(archive_path) as archive:
        assert "old.txt" not in archive.namelist()
