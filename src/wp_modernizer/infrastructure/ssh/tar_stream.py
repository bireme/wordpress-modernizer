"""Byte-preserving SSH stream and descriptor-relative, non-following tar extraction."""

from __future__ import annotations

import io
import os
import socket
import stat
import tarfile
import time
import uuid
from collections import deque
from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator

from wp_modernizer.domain.errors import TransferError


class ChannelStream(io.RawIOBase):
    def __init__(self, channel: Any, deadline: float) -> None:
        super().__init__()
        self.channel = channel
        self.deadline = deadline
        channel.settimeout(0.1)

    def check_timeout(self) -> None:
        if time.monotonic() >= self.deadline:
            raise TimeoutError

    def read(self, size: int = -1) -> bytes:
        while True:
            self.check_timeout()
            # Bound each drain so a noisy remote cannot starve stdout or the deadline.
            if self.channel.recv_stderr_ready():
                self.channel.recv_stderr(65536)
            try:
                return bytes(self.channel.recv(65536 if size < 0 else size))
            except socket.timeout:
                continue

    def exit_status(self) -> int:
        while True:
            self.check_timeout()
            if self.channel.recv_stderr_ready():
                self.channel.recv_stderr(65536)
            elif self.channel.exit_status_ready():
                return int(self.channel.recv_exit_status())
            else:
                time.sleep(0.01)


def _unsafe() -> TransferError:
    return TransferError("A transferência SSH/tar recusou um caminho, link ou tipo inseguro")


@contextmanager
def _directory(base: int, parts: tuple[str, ...]) -> Iterator[int]:
    """Never follow local links, including when a directory is replaced concurrently."""
    fd = os.dup(base)
    try:
        for part in parts:
            with suppress(FileExistsError):
                os.mkdir(part, mode=0o770, dir_fd=fd)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def extract_tree(
    archive: tarfile.TarFile,
    destination: Path,
    root_name: str,
    excluded: Callable[[PurePosixPath], bool],
    check_timeout: Callable[[], None],
) -> None:
    # Walk even the destination's ancestors without following symlinks.
    absolute = destination.absolute()
    anchor = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY)
    seen_root = False
    directories: dict[tuple[str, ...], tarfile.TarInfo] = {}
    links: dict[tuple[str, ...], str] = {}
    try:
        with _directory(anchor, absolute.parts[1:]) as base:
            for member in archive:
                check_timeout()
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\x00" in member.name
                    or not path.parts
                    or path.parts[0] != root_name
                ):
                    raise _unsafe()
                parts = path.parts
                if len(parts) == 1:
                    if not member.isdir():
                        raise _unsafe()
                    seen_root = True
                relative = PurePosixPath(*parts[1:])
                if excluded(relative):
                    continue
                if not (member.isdir() or member.isreg() or member.issym()):
                    raise _unsafe()
                with _directory(base, parts[:-1]) as parent:
                    name = parts[-1]
                    if member.isdir():
                        with _directory(parent, (name,)):
                            pass
                        directories[parts] = member
                    elif member.issym():
                        link_path = PurePosixPath(member.linkname)
                        if (
                            link_path.is_absolute()
                            or not member.linkname
                            or "\x00" in member.linkname
                        ):
                            raise _unsafe()
                        if parts in links:
                            raise _unsafe()
                        links[parts] = member.linkname
                    else:
                        _regular(archive, member, parent, name, check_timeout)
            if not seen_root:
                raise TransferError("A transferência SSH/tar não contém o diretório de origem")
            # Resolve the complete planned link graph before creating any links.
            # This catches chains that escape only after another link is installed.
            for parts, target in links.items():
                check_timeout()
                _validate_link(base, parts, target, links)
            for parts, target in links.items():
                check_timeout()
                with _directory(base, parts[:-1]) as parent:
                    os.symlink(target, parts[-1], dir_fd=parent)
            for parts, member in sorted(directories.items(), key=lambda item: -len(item[0])):
                check_timeout()
                with _directory(base, parts) as fd:
                    _metadata(fd, member)
    finally:
        os.close(anchor)


def _metadata(fd: int, member: tarfile.TarInfo) -> None:
    # Match rsync's u+w/g+w policy, without setuid/setgid/sticky or owner/group changes.
    os.fchmod(fd, (member.mode & 0o777) | 0o220)
    os.utime(fd, (member.mtime, member.mtime))


def _regular(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    parent: int,
    name: str,
    check_timeout: Callable[[], None],
) -> None:
    # Replacing a private temporary file avoids following existing symlinks/hardlinks.
    try:
        existing = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        existing = None
    if existing is not None and not stat.S_ISREG(existing.st_mode):
        raise _unsafe()
    temporary = ".wp-modernizer-" + uuid.uuid4().hex
    fd = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
    )
    try:
        with os.fdopen(fd, "wb") as output:
            payload = archive.extractfile(member)
            if payload is None:
                raise _unsafe()
            with payload:
                while True:
                    check_timeout()
                    chunk = payload.read(65536)
                    if not chunk:
                        break
                    output.write(chunk)
            output.flush()
            _metadata(output.fileno(), member)
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=parent)


def _validate_link(
    base: int, parts: tuple[str, ...], target: str, links: dict[tuple[str, ...], str]
) -> None:
    resolved = list(parts[:-1])
    pending = deque(PurePosixPath(target).parts)
    expansions = 0
    while pending:
        part = pending.popleft()
        if part == "..":
            if len(resolved) <= 1:
                raise _unsafe()
            resolved.pop()
            continue
        candidate = (*resolved, part)
        if candidate in links:
            expansions += 1
            if expansions > 40:
                raise _unsafe()
            pending.extendleft(reversed(PurePosixPath(links[candidate]).parts))
        else:
            _check_local_link_target(base, candidate)
            resolved.append(part)


def _check_local_link_target(base: int, resolved: tuple[str, ...]) -> None:
    # Refuse existing local symlinks in the target as well as in extraction paths.
    fd = os.dup(base)
    try:
        for index, part in enumerate(resolved):
            try:
                attributes = os.stat(part, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                break  # A dangling but contained link is safe.
            if stat.S_ISLNK(attributes.st_mode):
                raise _unsafe()
            if index < len(resolved) - 1:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
    finally:
        os.close(fd)
