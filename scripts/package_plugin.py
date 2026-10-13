import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
subprocess.run([sys.executable, str(root / "scripts/render_plugin.py")], check=True)
plugin = root / "dist/plugin"
meta = json.loads((plugin / ".claude-plugin/plugin.json").read_text())
(root / "dist").mkdir(exist_ok=True)
path = root / "dist" / f"{meta['name']}-{meta['version']}.zip"
with ZipFile(path, "w", ZIP_DEFLATED) as archive:
    for source in plugin.rglob("*"):
        if source.is_file():
            archive.write(source, source.relative_to(plugin))
print(path)
