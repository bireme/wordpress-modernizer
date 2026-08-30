import hashlib
import os
import shutil
from pathlib import Path


class LocalFileSystem:
    def exists(self, path: Path) -> bool:
        return path.exists()

    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace")

    def fingerprint(self, path: Path) -> str:
        digest = hashlib.sha256()
        if not path.exists():
            digest.update(b"missing")
            return digest.hexdigest()
        for root, directories, files in os.walk(path):
            directories[:] = sorted(item for item in directories if item != ".git")
            for name in sorted(files):
                item = Path(root) / name
                stat = item.stat()
                digest.update(str(item.relative_to(path)).encode())
                digest.update(str(stat.st_size).encode())
                digest.update(str(stat.st_mtime_ns).encode())
        return digest.hexdigest()

    def remove_tree(self, path: Path) -> None:
        shutil.rmtree(path)
