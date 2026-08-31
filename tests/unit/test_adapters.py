import socket
import stat
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

import paramiko
import pytest

from tests.fakes.core import FakeCommandResult, FakeCommandRunner
from wp_modernizer.config.models import DatabaseConfig, ServerConfig
from wp_modernizer.domain.enums import Environment
from wp_modernizer.domain.errors import (
    AuthenticationError,
    AuthenticationRefusedError,
    CommandTimeoutError,
    ConfigurationError,
    HostKeyVerificationError,
    InfrastructureError,
    PasswordAuthenticationError,
    RemoteHostUnreachableError,
    TransferError,
    UnsafeOperationError,
)
from wp_modernizer.infrastructure.filesystem import LocalFileSystem
from wp_modernizer.infrastructure.mysql.adapter import MySQLAdapter
from wp_modernizer.infrastructure.secrets import EnvironmentSecretProvider
from wp_modernizer.infrastructure.ssh.adapter import RSyncSSHAdapter
from wp_modernizer.infrastructure.ssh.password_adapter import PasswordSFTPAdapter
from wp_modernizer.infrastructure.ssh.router import FileTransferRouter
from wp_modernizer.infrastructure.wpcli.adapter import WPCLIAdapter


class Secrets:
    def __init__(self) -> None:
        self.calls = []

    def get(self, reference: str) -> str:
        self.calls.append(reference)
        return {"USER": "user", "PASS": "password"}[reference]


class FakeSFTP:
    def __init__(self) -> None:
        directory = stat.S_IFDIR | 0o750
        regular = stat.S_IFREG | 0o640
        self.nodes = {
            "/source": SimpleNamespace(st_mode=directory, st_atime=1, st_mtime=2),
            "/source/keep.txt": SimpleNamespace(st_mode=regular, st_atime=1, st_mtime=2),
            "/source/skip.sql": SimpleNamespace(st_mode=regular, st_atime=1, st_mtime=2),
            "/source/nested": SimpleNamespace(st_mode=directory, st_atime=1, st_mtime=2),
            "/source/nested/inside.txt": SimpleNamespace(st_mode=regular, st_atime=1, st_mtime=2),
        }
        self.files = {
            "/source/keep.txt": b"keep",
            "/source/skip.sql": b"skip",
            "/source/nested/inside.txt": b"nested",
        }
        self.timeout = None
        self.closed = False

    def get_channel(self) -> "FakeSFTP":
        return self

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def lstat(self, path: str) -> Any:
        try:
            return self.nodes[path]
        except KeyError as exc:
            raise OSError("missing remote entry") from exc

    def listdir_attr(self, path: str) -> list[Any]:
        root = PurePosixPath(path)
        names = {
            PurePosixPath(item).name for item in self.nodes if PurePosixPath(item).parent == root
        }
        return [SimpleNamespace(filename=name) for name in sorted(names)]

    def get(self, remote: str, local: str, callback: Any = None) -> None:
        Path(local).write_bytes(self.files[remote])
        if callback is not None:
            callback(len(self.files[remote]), len(self.files[remote]))

    def close(self) -> None:
        self.closed = True


class FakeSSHClient:
    def __init__(self, *, connect_error: Exception | None = None) -> None:
        self.connect_error = connect_error
        self.sftp = FakeSFTP()
        self.connect_kwargs: dict[str, Any] = {}
        self.policy = None
        self.loaded_system_keys = False
        self.loaded_host_keys = []
        self.closed = False

    def load_system_host_keys(self) -> None:
        self.loaded_system_keys = True

    def load_host_keys(self, filename: str) -> None:
        self.loaded_host_keys.append(filename)

    def set_missing_host_key_policy(self, policy: Any) -> None:
        self.policy = policy

    def connect(self, **kwargs: Any) -> None:
        self.connect_kwargs = kwargs
        if self.connect_error is not None:
            raise self.connect_error

    def open_sftp(self) -> FakeSFTP:
        return self.sftp

    def close(self) -> None:
        self.closed = True


def database() -> DatabaseConfig:
    return DatabaseConfig(host="db.example.invalid", username_secret="USER", password_secret="PASS")


def test_environment_secret_provider(monkeypatch) -> None:
    monkeypatch.setenv("PRESENT", "value")
    assert EnvironmentSecretProvider().get("PRESENT") == "value"
    with pytest.raises(ConfigurationError, match="MISSING"):
        EnvironmentSecretProvider().get("MISSING")


def test_mysql_schema_discovery_and_authentication_error() -> None:
    runner = FakeCommandRunner([FakeCommandResult(stdout="one\ntwo\n")])
    adapter = MySQLAdapter({"db": database()}, Secrets(), runner)
    assert adapter.list_schemas("db") == {"one", "two"}
    denied = MySQLAdapter(
        {"db": database()},
        Secrets(),
        FakeCommandRunner([FakeCommandResult(1, stderr="Access denied")]),
    )
    with pytest.raises(AuthenticationError):
        denied.list_schemas("db")


def test_mysql_never_imports_into_production() -> None:
    production = database().model_copy(update={"environment": Environment.PRODUCTION})
    runner = FakeCommandRunner()
    with pytest.raises(UnsafeOperationError, match="fora de TESTE"):
        MySQLAdapter({"db": production}, Secrets(), runner).import_dump(
            "db", "site", Path("/tmp/dump.sql"), "r"
        )
    assert runner.calls == []


def test_mysql_widget_snapshot_preserves_binary_and_rejects_bad_table() -> None:
    runner = FakeCommandRunner(
        [
            FakeCommandResult(stdout="wp_options\n"),
            FakeCommandResult(stdout="widget_text\t00ff\tyes\n"),
        ]
    )
    snapshot = MySQLAdapter({"db": database()}, Secrets(), runner).snapshot_widgets("db", "site")
    assert snapshot.options[0].value == b"\x00\xff"
    bad = FakeCommandRunner([FakeCommandResult(stdout="bad-name_options\n")])
    with pytest.raises(InfrastructureError, match="identificador de tabela inseguro"):
        MySQLAdapter({"db": database()}, Secrets(), bad).snapshot_widgets("db", "site")


def test_wpcli_adapter_dry_run_multisite_and_failure() -> None:
    runner = FakeCommandRunner([FakeCommandResult(stdout="changed")])
    adapter = WPCLIAdapter(runner)
    assert (
        adapter.search_replace(
            Path("/site"), "https://old", "https://new", dry_run=True, multisite=True, run_id="r"
        )
        == "changed"
    )
    assert "--dry-run" in runner.calls[0] and "--network" in runner.calls[0]
    with pytest.raises(Exception, match="failed"):
        WPCLIAdapter(FakeCommandRunner([FakeCommandResult(1, stderr="failed")])).update(
            Path("/site"), ["core", "update"], "r"
        )


def test_wpcli_writes_config_values_via_stdin_not_argv() -> None:
    runner = FakeCommandRunner()
    WPCLIAdapter(runner).set_config(Path("/site"), {"DB_PASSWORD": "never-in-argv"}, "run-1")
    assert "--prompt=value" in runner.calls[0]
    assert "DB_PASSWORD" in runner.calls[0]
    assert "never-in-argv" not in runner.calls[0]


def test_key_ssh_adapter_continues_to_use_rsync_without_credentials_in_argv() -> None:
    key = ServerConfig(
        host="source.example.invalid",
        environment=Environment.PRODUCTION,
        username_secret="USER",
        private_key=Path("/key"),
    )
    runner = FakeCommandRunner()
    RSyncSSHAdapter({"s": key}, Secrets(), runner).copy_from(
        "s", Path("/source"), Path("/target"), [], "r"
    )
    assert runner.calls[0][0] == "rsync"
    assert "user" not in runner.calls[0]


def password_server(**updates: Any) -> ServerConfig:
    values = {
        "host": "source.example.invalid",
        "environment": Environment.PRODUCTION,
        "username_secret": "USER",
        "authentication": "password",
        "password_secret": "PASS",
        "host_key_policy": "strict",
    }
    values.update(updates)
    return ServerConfig(**values)


def test_password_sftp_resolves_secrets_by_api_and_copies_with_exclusions(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client = FakeSSHClient()
    secrets = Secrets()
    adapter = PasswordSFTPAdapter(
        {"s": password_server(known_hosts_file=tmp_path / "known_hosts")},
        secrets,
        client_factory=lambda: client,
    )

    adapter.copy_from(
        "s",
        Path("/source"),
        tmp_path,
        [Path("*.sql"), Path("/source/nested")],
        "run-1",
    )

    assert secrets.calls == ["USER", "PASS"]
    assert client.connect_kwargs["username"] == "user"
    assert client.connect_kwargs["password"] == "password"
    assert client.connect_kwargs["allow_agent"] is False
    assert client.connect_kwargs["look_for_keys"] is False
    assert client.loaded_system_keys is True
    assert client.loaded_host_keys == [str(tmp_path / "known_hosts")]
    assert client.policy.__class__.__name__ == "_RejectUnknownHostKey"
    assert (tmp_path / "source/keep.txt").read_bytes() == b"keep"
    assert not (tmp_path / "source/skip.sql").exists()
    assert not (tmp_path / "source/nested").exists()
    assert "password" not in caplog.text


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (paramiko.AuthenticationException("password"), PasswordAuthenticationError),
        (
            paramiko.BadAuthenticationType("refused", ["publickey"]),
            AuthenticationRefusedError,
        ),
        (socket.timeout(), CommandTimeoutError),
        (HostKeyVerificationError("unknown"), HostKeyVerificationError),
        (OSError("network unreachable"), RemoteHostUnreachableError),
    ],
)
def test_password_sftp_reports_connection_failures_without_secret(
    tmp_path: Path, error: Exception, expected: type[Exception]
) -> None:
    client = FakeSSHClient(connect_error=error)
    adapter = PasswordSFTPAdapter(
        {"s": password_server()}, Secrets(), client_factory=lambda: client
    )
    with pytest.raises(expected) as raised:
        adapter.copy_from("s", Path("/source"), tmp_path, [], "run-1")
    assert "password" not in str(raised.value).lower()


def test_password_sftp_reports_transfer_failure(tmp_path: Path) -> None:
    client = FakeSSHClient()
    client.sftp.nodes.pop("/source")
    adapter = PasswordSFTPAdapter(
        {"s": password_server()}, Secrets(), client_factory=lambda: client
    )
    with pytest.raises(TransferError, match="transferência SFTP"):
        adapter.copy_from("s", Path("/source"), tmp_path, [], "run-1")


def test_file_transfer_router_selects_authentication_explicitly(tmp_path: Path) -> None:
    key = password_server().model_copy(update={"authentication": "key", "password_secret": None})
    password = password_server()
    runner = FakeCommandRunner()
    client = FakeSSHClient()
    key_transport = RSyncSSHAdapter({"key": key, "password": password}, Secrets(), runner)
    password_transport = PasswordSFTPAdapter(
        {"key": key, "password": password}, Secrets(), client_factory=lambda: client
    )
    router = FileTransferRouter(
        {"key": key, "password": password}, key_transport, password_transport
    )

    router.copy_from("key", Path("/source"), tmp_path, [], "run-key")
    router.copy_from("password", Path("/source"), tmp_path, [], "run-password")

    assert runner.calls[0][0] == "rsync"
    assert client.connect_kwargs["password"] == "password"


def test_local_filesystem_fingerprint_changes_and_remove(tmp_path: Path) -> None:
    root = tmp_path / "site"
    root.mkdir()
    item = root / "file"
    item.write_text("one")
    filesystem = LocalFileSystem()
    first = filesystem.fingerprint(root)
    item.write_text("a longer value")
    assert filesystem.fingerprint(root) != first
    filesystem.remove_tree(root)
    assert not root.exists()
