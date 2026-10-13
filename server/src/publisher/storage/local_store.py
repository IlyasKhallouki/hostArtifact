import fcntl
import json
from pathlib import Path

from publisher.models import empty_state


class LocalMetadataStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "metadata.json"

    def read(self):
        with (self.root / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            return (
                json.loads(self.path.read_text())
                if self.path.exists()
                else empty_state()
            )

    def transaction(self, change):
        with (self.root / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = (
                json.loads(self.path.read_text())
                if self.path.exists()
                else empty_state()
            )
            result = change(state)
            temporary = self.root / "metadata.tmp"
            temporary.write_text(json.dumps(state))
            temporary.replace(self.path)
            return result


class LocalBlobStore:
    def __init__(self, root: Path):
        self.root = root / "blobs"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, path: str) -> Path:
        target = (self.root / path).resolve()
        if not target.is_relative_to(self.root.resolve()):
            raise ValueError("Invalid blob path")
        return target

    def put(self, path: str, html: str):
        target = self._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("x") as stream:
                stream.write(html)
        except FileExistsError:
            if target.read_text() != html:
                raise ValueError("Immutable blob already exists") from None

    def get(self, path: str) -> str:
        return self._path(path).read_text()

    def delete_prefix(self, prefix: str):
        for path in self._path(prefix).rglob("*"):
            if path.is_file():
                path.unlink()
