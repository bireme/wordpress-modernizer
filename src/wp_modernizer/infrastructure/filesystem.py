import hashlib
import os
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path

from wp_modernizer.domain.errors import InfrastructureError


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

    def is_symlink(self, path: Path) -> bool:
        return path.is_symlink()

    def create_temporary_directory(self, parent: Path, prefix: str) -> Path:
        return Path(tempfile.mkdtemp(dir=parent, prefix=prefix))

    def move(self, source: Path, destination: Path) -> None:
        source.replace(destination)

    def create_immutable_backup(self, source: Path, destination: Path) -> str:
        """Copy, verify and make a directory snapshot read-only before publishing it."""
        if not source.is_dir() or source.is_symlink():
            raise InfrastructureError("a origem do backup não é um diretório regular seguro")
        if destination.exists() or destination.is_symlink():
            raise InfrastructureError("o caminho final do backup já existe")
        common = Path(os.path.commonpath((source.absolute(), destination.absolute())))
        if common == source or source in destination.parents:
            raise InfrastructureError("o backup deve ficar fora da árvore substituída")
        current = destination.parent
        while current != common.parent:
            if current.is_symlink():
                raise InfrastructureError("o caminho do backup contém um link simbólico")
            if current == common:
                break
            current = current.parent
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(dir=destination.parent, prefix=f".{destination.name}-creating-")
        )
        # copytree requires the target not to exist; mkdtemp reserves a collision-free name.
        temporary.rmdir()
        try:
            before = self._content_fingerprint(source)
            shutil.copytree(source, temporary, symlinks=True, copy_function=shutil.copy2)
            copied = self._content_fingerprint(temporary)
            after = self._content_fingerprint(source)
            if before != copied or before != after:
                raise InfrastructureError(
                    "a árvore mudou durante o backup ou a verificação de conteúdo falhou"
                )
            self._make_read_only(temporary)
            temporary.replace(destination)
            if not self.verify_backup(destination, before):
                raise InfrastructureError("o backup publicado não passou na verificação final")
            return before
        except Exception:
            with suppress(OSError):
                if temporary.exists():
                    shutil.rmtree(temporary)
            raise

    def verify_backup(self, path: Path, fingerprint: str) -> bool:
        if not path.is_dir() or path.is_symlink():
            return False
        try:
            return self._content_fingerprint(path) == fingerprint and self._is_read_only(path)
        except OSError:
            return False

    @staticmethod
    def _content_fingerprint(path: Path) -> str:
        digest = hashlib.sha256()
        for item in sorted(path.rglob("*"), key=lambda value: value.relative_to(path).as_posix()):
            relative = item.relative_to(path).as_posix()
            digest.update(relative.encode("utf-8", errors="surrogateescape"))
            if item.is_symlink():
                digest.update(b"L")
                digest.update(os.readlink(item).encode("utf-8", errors="surrogateescape"))
            elif item.is_dir():
                digest.update(b"D")
            elif item.is_file():
                digest.update(b"F")
                with item.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
            else:
                raise InfrastructureError("a árvore contém um tipo de arquivo não suportado")
        return digest.hexdigest()

    @staticmethod
    def _make_read_only(path: Path) -> None:
        entries = sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True)
        for item in (*entries, path):
            if not item.is_symlink():
                item.chmod(item.stat().st_mode & ~0o222)

    @staticmethod
    def _is_read_only(path: Path) -> bool:
        return all(
            item.is_symlink() or item.stat().st_mode & 0o222 == 0
            for item in (path, *path.rglob("*"))
        )
