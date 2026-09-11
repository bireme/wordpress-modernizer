import io
import os
import socket
import tarfile
import traceback
from pathlib import Path

import paramiko
import pytest

from tests.unit.test_adapters import FakeSSHClient, FakeTarChannel, Secrets, password_server
from wp_modernizer.domain.errors import CommandTimeoutError, TransferError
from wp_modernizer.infrastructure.ssh.password_adapter import PasswordSFTPAdapter


def archive_bytes(entries):
    stream = io.BytesIO()
    with tarfile.open(
        fileobj=stream,
        mode="w",
        format=tarfile.GNU_FORMAT,
        encoding="utf-8",
        errors="surrogateescape",
    ) as archive:
        root = tarfile.TarInfo("source")
        root.type = tarfile.DIRTYPE
        root.mode = 0o750
        archive.addfile(root)
        for raw, kind, data in entries:
            member = tarfile.TarInfo(os.fsdecode(raw))
            member.type = kind
            member.mode = 0o6750
            if kind == tarfile.REGTYPE:
                member.size = len(data)
            elif kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                member.linkname = os.fsdecode(data)
            archive.addfile(member, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
    return stream.getvalue()


def setup_adapter(payload):
    client = FakeSSHClient()
    client.channel = FakeTarChannel(payload)

    # Prove the SFTP decoder is never involved in tree transfer.
    def forbidden():
        raise AssertionError("SFTP tree traversal")

    client.open_sftp = forbidden
    adapter = PasswordSFTPAdapter(
        {"s": password_server()}, Secrets(), client_factory=lambda: client
    )
    return adapter, client


def test_legacy_names_preserved_byte_for_byte(tmp_path):
    names = [b"Centro de Documenta\xe7\xe3o.bmp", b"M\xe9todosIAL.pdf"]
    entries = [(b"source/" + n, tarfile.REGTYPE, n) for n in names]
    entries += [
        (b"source/1\xaa conf", tarfile.DIRTYPE, b""),
        (b"source/1\xaa conf/" + names[0], tarfile.REGTYPE, b"nested"),
    ]
    adapter, client = setup_adapter(archive_bytes(entries))
    adapter.copy_from("s", Path("/source"), tmp_path, [], "r")
    base = os.fsencode(tmp_path) + b"/source"
    assert set(os.listdir(base)) == {*names, b"1\xaa conf"}
    for name in names:
        with open(base + b"/" + name, "rb") as f:
            assert f.read() == name
    with open(base + b"/1\xaa conf/" + names[0], "rb") as f:
        assert f.read() == b"nested"
    assert client.channel.closed and client.closed
    assert b"password" not in client.channel.command
    assert (tmp_path / "source" / os.fsdecode(names[0])).stat().st_mode & 0o7777 == 0o770


@pytest.mark.parametrize(
    "name,kind,target",
    [
        (b"../escape", tarfile.REGTYPE, b"bad"),
        (b"/tmp/escape", tarfile.REGTYPE, b"bad"),
        (b"source/../../escape", tarfile.REGTYPE, b"bad"),
        (b"other/file", tarfile.REGTYPE, b"bad"),
        (b"source/link", tarfile.SYMTYPE, b"../../escape"),
        (b"source/link", tarfile.LNKTYPE, b"source/file"),
        (b"source/fifo", tarfile.FIFOTYPE, b""),
    ],
)
def test_rejects_unsafe_members(tmp_path, name, kind, target):
    adapter, client = setup_adapter(archive_bytes([(name, kind, target)]))
    with pytest.raises(TransferError):
        adapter.copy_from("s", Path("/source"), tmp_path, [], "r")
    assert client.channel.closed
    assert not (tmp_path / "escape").exists()


def test_absolute_symlink_is_preserved(tmp_path):
    adapter, _ = setup_adapter(
        archive_bytes(
            [
                (b"source/link", tarfile.SYMTYPE, b"/tmp/escape"),
            ]
        )
    )

    adapter.copy_from("s", Path("/source"), tmp_path, [], "r")

    assert os.readlink(tmp_path / "source/link") == "/tmp/escape"


def test_safe_link_preserved_but_never_followed(tmp_path):
    entries = [(b"source/dir", tarfile.DIRTYPE, b""), (b"source/link", tarfile.SYMTYPE, b"dir")]
    adapter, _ = setup_adapter(archive_bytes(entries))
    adapter.copy_from("s", Path("/source"), tmp_path, [], "r")
    assert os.readlink(tmp_path / "source/link") == "dir"
    entries.append((b"source/link/file", tarfile.REGTYPE, b"bad"))
    adapter, _ = setup_adapter(archive_bytes(entries))
    with pytest.raises(TransferError):
        adapter.copy_from("s", Path("/source"), tmp_path / "second", [], "r")
    assert not (tmp_path / "second/source/dir/file").exists()


@pytest.mark.parametrize("level", ["parent", "root", "directory", "file"])
def test_local_links_cannot_escape(tmp_path, level):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file").write_bytes(b"original")
    destination = tmp_path / "dest"
    destination.mkdir()
    location = {
        "parent": destination / "alias",
        "root": destination / "source",
        "directory": destination / "source/dir",
        "file": destination / "source/dir/file",
    }[level]
    location.parent.mkdir(parents=True, exist_ok=True)
    location.symlink_to(outside / "file" if level == "file" else outside)
    adapter, _ = setup_adapter(archive_bytes([(b"source/dir/file", tarfile.REGTYPE, b"bad")]))
    with pytest.raises(TransferError):
        adapter.copy_from(
            "s", Path("/source"), location if level == "parent" else destination, [], "r"
        )
    assert (outside / "file").read_bytes() == b"original"
    assert set(os.listdir(outside)) == {"file"}


def test_excludes_legacy_directory_and_nested_patterns(tmp_path):
    entries = [
        (b"source/1\xaa conf", tarfile.DIRTYPE, b""),
        (b"source/1\xaa conf/file", tarfile.REGTYPE, b"skip"),
        (b"source/deep/cache/file", tarfile.REGTYPE, b"skip"),
        (b"source/deep/a.sql", tarfile.REGTYPE, b"skip"),
        (b"source/keep", tarfile.REGTYPE, b"keep"),
    ]
    adapter, _ = setup_adapter(archive_bytes(entries))
    adapter.copy_from(
        "s",
        Path("/source"),
        tmp_path,
        [Path(os.fsdecode(b"/source/1\xaa conf")), Path("cache"), Path("*.sql")],
        "r",
    )
    assert os.listdir(tmp_path / "source") == ["keep"]


@pytest.mark.parametrize("failure", ["status", "truncated", "ssh", "timeout", "deadline"])
def test_transport_errors_are_safe_and_close_channel(tmp_path, caplog, failure):
    adapter, client = setup_adapter(
        archive_bytes([(b"source/file", tarfile.REGTYPE, b"x" * 20000)])
    )
    client.channel.stderr = b"password user" * 20000
    expected = TransferError
    if failure == "status":
        client.channel.status = 2
    elif failure == "truncated":
        client.channel.payload = io.BytesIO(client.channel.payload.getvalue()[:2000])
    elif failure == "ssh":
        client.channel.error = paramiko.SSHException("password user")
    else:
        adapter.TRANSFER_TIMEOUT_SECONDS = 0.02
        expected = CommandTimeoutError
        if failure == "timeout":
            client.channel.error = socket.timeout("password user")
        else:
            client.channel.exit_status_ready = lambda: False
    with pytest.raises(expected) as raised:
        adapter.copy_from("s", Path("/source"), tmp_path, [], "r")
    diagnostic = "".join(traceback.format_exception(raised.type, raised.value, raised.tb))
    assert "password user" not in diagnostic + caplog.text
    assert client.closed and client.channel.closed


@pytest.mark.parametrize("chain", [False, True])
def test_link_escape_through_intermediate_component_is_rejected(tmp_path, chain):
    entries = [
        (b"source/dir", tarfile.DIRTYPE, b""),
        (b"source/escape", tarfile.SYMTYPE, b"dir/up/../outside"),
    ]
    if chain:
        entries.append((b"source/dir/up", tarfile.SYMTYPE, b".."))
    else:
        (tmp_path / "source/dir").mkdir(parents=True)
        (tmp_path / "source/dir/up").symlink_to(tmp_path)
    adapter, _ = setup_adapter(archive_bytes(entries))
    with pytest.raises(TransferError):
        adapter.copy_from("s", Path("/source"), tmp_path, [], "r")
    assert not (tmp_path / "source/escape").is_symlink()


def test_safe_parent_link_and_legacy_target(tmp_path):
    adapter, _ = setup_adapter(
        archive_bytes(
            [
                (b"source/M\xe9todosIAL.pdf", tarfile.REGTYPE, b"pdf"),
                (b"source/dir/link", tarfile.SYMTYPE, b"../M\xe9todosIAL.pdf"),
            ]
        )
    )
    adapter.copy_from("s", Path("/source"), tmp_path, [], "r")
    assert os.readlink(os.fsencode(tmp_path) + b"/source/dir/link") == b"../M\xe9todosIAL.pdf"
    assert (tmp_path / "source/dir/link").read_bytes() == b"pdf"


def test_real_gnu_tar_with_legacy_long_names_and_quoted_source(tmp_path):
    import shutil
    import subprocess

    if shutil.which("tar") is None:
        pytest.skip("GNU tar is not installed")
    remote = tmp_path / "remote" / os.fsdecode(b"-site'\xe7\n")
    remote.mkdir(parents=True)
    long_name = b"x" * 130 + b"\xe7.pdf"
    (remote / os.fsdecode(long_name)).write_bytes(b"long")
    os.link(remote / os.fsdecode(long_name), remote / "hardlink")
    adapter, client = setup_adapter(b"")

    def exec_local(command):
        client.channel.command = command
        result = subprocess.run(  # noqa: S603
            ["sh", "-c", command],  # noqa: S607
            capture_output=True,
            check=True,
        )
        client.channel.payload = io.BytesIO(result.stdout)
        client.channel.stderr = result.stderr

    client.channel.exec_command = exec_local
    destination = tmp_path / "destination"
    adapter.copy_from("s", remote, destination, [], "r")
    copied = destination / remote.name
    assert set(os.listdir(os.fsencode(copied))) == {long_name, b"hardlink"}
    assert (copied / os.fsdecode(long_name)).read_bytes() == b"long"
    assert (copied / "hardlink").read_bytes() == b"long"


def test_hardlinked_local_file_does_not_mutate_outside_destination(tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"original")
    (tmp_path / "source").mkdir()
    os.link(outside, tmp_path / "source/file")
    adapter, _ = setup_adapter(archive_bytes([(b"source/file", tarfile.REGTYPE, b"new")]))
    adapter.copy_from("s", Path("/source"), tmp_path, [], "r")
    assert outside.read_bytes() == b"original"
    assert (tmp_path / "source/file").read_bytes() == b"new"


def test_timeout_closes_channel_during_exec_acknowledgement(tmp_path):
    import threading

    adapter, client = setup_adapter(b"")
    closed = threading.Event()

    def close():
        client.channel.closed = True
        closed.set()

    def blocked_exec(command):
        assert closed.wait(1), "deadline did not close the blocked channel"
        raise paramiko.SSHException("password user")

    client.channel.close = close
    client.channel.exec_command = blocked_exec
    adapter.TRANSFER_TIMEOUT_SECONDS = 0.02
    with pytest.raises(CommandTimeoutError):
        adapter.copy_from("s", Path("/source"), tmp_path, [], "r")
    assert client.closed and client.channel.closed


@pytest.mark.parametrize("source", ["relative", "/", "/source/../escape"])
def test_invalid_source_rejected_before_connecting(tmp_path, source):
    from wp_modernizer.domain.errors import ConfigurationError

    adapter, client = setup_adapter(b"")
    with pytest.raises(ConfigurationError):
        adapter.copy_from("s", Path(source), tmp_path, [], "r")
    assert client.connect_kwargs == {}


def test_link_cycle_is_rejected(tmp_path):
    adapter, _ = setup_adapter(
        archive_bytes([(b"source/a", tarfile.SYMTYPE, b"b"), (b"source/b", tarfile.SYMTYPE, b"a")])
    )
    with pytest.raises(TransferError):
        adapter.copy_from("s", Path("/source"), tmp_path, [], "r")
    assert not (tmp_path / "source/a").is_symlink()


def test_open_session_timeout_is_sanitized(tmp_path, caplog):
    adapter, client = setup_adapter(b"")

    def timed_out(timeout):
        assert 0 < timeout <= adapter.TRANSFER_TIMEOUT_SECONDS
        raise socket.timeout("private transport diagnostic")

    client.open_session = timed_out
    with pytest.raises(CommandTimeoutError) as raised:
        adapter.copy_from("s", Path("/source"), tmp_path, [], "r")
    diagnostic = "".join(traceback.format_exception(raised.type, raised.value, raised.tb))
    assert "private transport diagnostic" not in diagnostic + caplog.text
    assert raised.value.__suppress_context__
    assert client.closed
    assert not client.channel.command


@pytest.mark.parametrize("apply_remote", [False, True])
def test_remote_exclusions_and_local_second_barrier(tmp_path, apply_remote):
    import shutil
    import subprocess

    if shutil.which("tar") is None:
        pytest.skip("GNU tar is not installed")
    remote = tmp_path / "remote" / "source[*]'"
    remote.mkdir(parents=True)
    excluded = [
        "dump.sql",
        "deep/dump.sql",
        "deep/.wp-modernizer/state",
        ".wp-modernizer/state",
        "branch/child/file",
        "deep/cache/file",
        os.fsdecode(b"legacy\xe7/file"),
        "$(touch INJECTED)'/file",
        "back\\slash/file",
    ]
    kept = ["keep", "deep/keep", "deep/branch/child/keep", os.fsdecode(b"keep\xe7.pdf")]
    for name in excluded + kept:
        target = remote / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(name.encode("utf-8", "surrogateescape"))
    adapter, client = setup_adapter(b"")
    transmitted = []

    def exec_local(command):
        import shlex

        client.channel.command = command
        if not apply_remote:
            command = os.fsencode(
                "LC_ALL=C tar --format=gnu -cf - -C "
                + shlex.quote(str(remote.parent))
                + " -- "
                + shlex.quote(remote.name)
            )
        result = subprocess.run(  # noqa: S603
            ["sh", "-c", command],  # noqa: S607
            capture_output=True,
            check=True,
            cwd=tmp_path,
        )
        with tarfile.open(fileobj=io.BytesIO(result.stdout), errors="surrogateescape") as archive:
            transmitted.extend(member.name for member in archive if member.isfile())
        client.channel.payload = io.BytesIO(result.stdout)

    client.channel.exec_command = exec_local
    destination = tmp_path / "destination"
    adapter.copy_from(
        "s",
        remote,
        destination,
        [
            Path("*.sql"),
            Path(".wp-modernizer"),
            remote / "branch/child",
            Path("cache"),
            Path(os.fsdecode(b"legacy\xe7")),
            Path("$(touch INJECTED)'"),
            Path("back\\slash"),
        ],
        "r",
    )
    assert set(transmitted) == {
        f"{remote.name}/{name}" for name in (kept if apply_remote else kept + excluded)
    }
    copied = destination / remote.name
    assert all(not (copied / name).exists() for name in excluded)
    assert all((copied / name).read_bytes() == os.fsencode(name) for name in kept)
    assert not (tmp_path / "INJECTED").exists()


@pytest.mark.parametrize(
    "pattern,name",
    [
        ("?.sql", "é.sql"),
        ("[ab].sql", "a.sql"),
        ("deep/*.sql", "deep/a.sql"),
        ("é*", "é.pdf"),
        ("back\\*", "back\\file"),
    ],
)
def test_unsupported_remote_globs_remain_local(tmp_path, pattern, name):
    adapter, client = setup_adapter(
        archive_bytes(
            [
                (os.fsencode("source/" + name), tarfile.REGTYPE, b"excluded"),
                (b"source/keep", tarfile.REGTYPE, b"keep"),
            ]
        )
    )
    adapter.copy_from("s", Path("/source"), tmp_path, [Path(pattern)], "r")
    assert b"--exclude=" not in client.channel.command
    assert not (tmp_path / "source" / name).exists()
    assert (tmp_path / "source/keep").read_bytes() == b"keep"
