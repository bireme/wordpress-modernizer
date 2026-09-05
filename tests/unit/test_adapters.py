import io
import socket
import stat
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

import paramiko
import pytest

from tests.fakes.core import FakeCommandResult, FakeCommandRunner
from wp_modernizer.config.models import DatabaseConfig, ServerConfig
from wp_modernizer.domain.enums import (
    DatabaseAvailabilityStatus,
    Environment,
    PendingOperationType,
    StepStatus,
)
from wp_modernizer.domain.errors import (
    AuthenticationError,
    AuthenticationRefusedError,
    CommandTimeoutError,
    ConfigurationError,
    DatabaseNotFoundError,
    HostKeyVerificationError,
    InfrastructureError,
    PasswordAuthenticationError,
    RemoteHostUnreachableError,
    TransferError,
    UnsafeOperationError,
    WordPressUnavailableError,
)
from wp_modernizer.domain.models import PendingOperation, PlannedStep
from wp_modernizer.domain.widgets import WidgetOption, WidgetSnapshot
from wp_modernizer.infrastructure.filesystem import LocalFileSystem
from wp_modernizer.infrastructure.mysql.adapter import MySQLAdapter
from wp_modernizer.infrastructure.runtime_operations import RuntimeOperations
from wp_modernizer.infrastructure.secrets import EnvironmentSecretProvider
from wp_modernizer.infrastructure.ssh.adapter import RSyncSSHAdapter
from wp_modernizer.infrastructure.ssh.password_adapter import PasswordSFTPAdapter
from wp_modernizer.infrastructure.ssh.router import FileTransferRouter
from wp_modernizer.infrastructure.wp_config_writer import WordPressConfigWriter
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
            "/source/htdocs/wp-config.php": (
                b"<?php\n"
                b"define('DB_NAME', 'wp_portal_prod');\n"
                b"define('DB_HOST', 'prod-db:3307');\n"
                b"define('DB_USER', 'prod-user');\n"
                b"define('DB_PASSWORD', 'prod-password');\n"
                b"$table_prefix = 'wp_';\n"
            ),
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

    def open(self, remote: str, mode: str) -> io.BytesIO:
        assert mode == "rb"
        try:
            return io.BytesIO(self.files[remote])
        except KeyError as exc:
            raise OSError("missing remote file") from exc

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
        self.exec_calls: list[tuple[str, float]] = []
        self.exec_stdout = b""
        self.exec_stderr = b""
        self.exec_status = 0

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

    def exec_command(self, command: str, timeout: float):
        self.exec_calls.append((command, timeout))

        class Stream:
            def __init__(self, payload: bytes, status: int = 0) -> None:
                self.payload = payload
                self.channel = self
                self.status = status

            def read(self) -> bytes:
                return self.payload

            def recv_exit_status(self) -> int:
                return self.status

        return (
            Stream(b""),
            Stream(self.exec_stdout, self.exec_status),
            Stream(self.exec_stderr),
        )

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


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (FakeCommandResult(stdout="1\n"), DatabaseAvailabilityStatus.AVAILABLE),
        (
            FakeCommandResult(1, stderr="ERROR 1045: Access denied for password secret-value"),
            DatabaseAvailabilityStatus.AUTHENTICATION_DENIED,
        ),
        (
            FakeCommandResult(1, stderr="ERROR 1049: Unknown database 'missing'"),
            DatabaseAvailabilityStatus.SCHEMA_NOT_FOUND,
        ),
        (
            FakeCommandResult(1, stderr="ERROR 2003: Can't connect to MySQL server"),
            DatabaseAvailabilityStatus.ENDPOINT_UNAVAILABLE,
        ),
        (
            FakeCommandResult(1, stderr="unexpected secret-value"),
            DatabaseAvailabilityStatus.UNKNOWN,
        ),
    ],
)
def test_mysql_database_probe_returns_redacted_evidence(result, expected) -> None:
    probe = MySQLAdapter({"db": database()}, Secrets(), FakeCommandRunner([result])).probe_database(
        "db", "site"
    )
    assert probe.status is expected
    assert "secret-value" not in probe.detail


def test_mysql_database_probe_reports_insufficient_configuration_without_command() -> None:
    runner = FakeCommandRunner()
    probe = MySQLAdapter({"db": database()}, Secrets(), runner).probe_database("db", "")
    assert probe.status is DatabaseAvailabilityStatus.CONFIGURATION_INSUFFICIENT
    assert runner.calls == []


def test_mysql_never_imports_into_production() -> None:
    production = database().model_copy(update={"environment": Environment.PRODUCTION})
    runner = FakeCommandRunner()
    with pytest.raises(UnsafeOperationError, match="fora de TESTE"):
        MySQLAdapter({"db": production}, Secrets(), runner).import_dump(
            "db", "site", Path("/tmp/dump.sql"), "r"
        )
    assert runner.calls == []


def test_mysql_reads_source_site_url_with_a_fixed_select_only() -> None:
    production = database().model_copy(update={"environment": Environment.PRODUCTION})
    runner = FakeCommandRunner([FakeCommandResult(stdout="https://portal.bireme.org\n")])
    value = MySQLAdapter({"production": production}, Secrets(), runner).read_site_url(
        "production", "wordpress", "wp_"
    )
    assert value == "https://portal.bireme.org"
    sql = runner.calls[0][-1]
    assert sql == ("SELECT option_value FROM `wp_options` WHERE option_name='siteurl' LIMIT 2")
    assert all(keyword not in sql.upper() for keyword in ("UPDATE", "INSERT", "DELETE"))


def test_mysql_site_url_rejects_bad_prefix_missing_and_ambiguous_rows() -> None:
    production = database().model_copy(update={"environment": Environment.PRODUCTION})
    no_calls = FakeCommandRunner()
    adapter = MySQLAdapter({"production": production}, Secrets(), no_calls)
    with pytest.raises(ConfigurationError, match="prefixo"):
        adapter.read_site_url("production", "wordpress", "wp_;DROP")
    assert no_calls.calls == []

    missing = MySQLAdapter(
        {"production": production}, Secrets(), FakeCommandRunner([FakeCommandResult()])
    )
    with pytest.raises(DatabaseNotFoundError, match="siteurl"):
        missing.read_site_url("production", "wordpress", "wp_")

    ambiguous = MySQLAdapter(
        {"production": production},
        Secrets(),
        FakeCommandRunner(
            [FakeCommandResult(stdout="https://one.bireme.org\nhttps://two.bireme.org\n")]
        ),
    )
    with pytest.raises(InfrastructureError, match="ambígua"):
        ambiguous.read_site_url("production", "wordpress", "wp_")


def test_mysql_import_requires_preexisting_test_schema() -> None:
    runner = FakeCommandRunner([FakeCommandResult(stdout="another_schema\n")])
    with pytest.raises(DatabaseNotFoundError, match="infraestrutura deve provisioná-lo"):
        MySQLAdapter({"db": database()}, Secrets(), runner).import_dump(
            "db", "wp_portal_tst", Path("/tmp/dump.sql"), "r"
        )
    assert len(runner.calls) == 1
    assert any("INFORMATION_SCHEMA.SCHEMATA" in argument for argument in runner.calls[0])


def test_mysql_imports_only_after_test_schema_is_confirmed() -> None:
    runner = FakeCommandRunner([FakeCommandResult(stdout="wp_portal_tst\n"), FakeCommandResult()])
    MySQLAdapter({"db": database()}, Secrets(), runner).import_dump(
        "db", "wp_portal_tst", Path("/tmp/dump.sql"), "r"
    )
    assert len(runner.calls) == 2
    assert "--execute" in runner.calls[0]
    assert runner.calls[1][-1] == "wp_portal_tst"


def test_mysql_widget_snapshot_preserves_binary_and_rejects_bad_table() -> None:
    runner = FakeCommandRunner(
        [
            FakeCommandResult(stdout="wp_options\n"),
            FakeCommandResult(stdout="widget_text\t00ff\tyes\n"),
        ]
    )
    snapshot = MySQLAdapter({"db": database()}, Secrets(), runner).snapshot_widgets("db", "site")
    assert snapshot.options[0].value == b"\x00\xff"
    snapshot_query = next(
        argument for argument in runner.calls[1] if argument.startswith("SELECT option_name")
    )
    assert "sidebars_widgets" in snapshot_query
    assert "widget\\_%" in snapshot_query
    assert "template" in snapshot_query and "stylesheet" in snapshot_query
    bad = FakeCommandRunner([FakeCommandResult(stdout="bad-name_options\n")])
    with pytest.raises(InfrastructureError, match="identificador de tabela inseguro"):
        MySQLAdapter({"db": database()}, Secrets(), bad).snapshot_widgets("db", "site")


def test_mysql_restores_existing_test_widget_snapshot_transactionally() -> None:
    class ScriptRunner(FakeCommandRunner):
        script = ""

        def run(self, argv, **kwargs):
            stdin_path = kwargs.get("stdin_path")
            if stdin_path is not None:
                self.script = stdin_path.read_text()
            return super().run(argv, **kwargs)

    reference = WidgetSnapshot.from_options(
        [WidgetOption("wp_options", "widget_text", b"serialized\x00reference", "yes")]
    )
    runner = ScriptRunner(
        [
            FakeCommandResult(stdout="site\n"),
            FakeCommandResult(stdout="wp_options\n"),
            FakeCommandResult(stdout="widget_text\t6166746572\tyes\n"),
            FakeCommandResult(),
        ]
    )
    MySQLAdapter({"db": database()}, Secrets(), runner).restore_widgets(
        "db", "site", reference, "run-1"
    )
    assert "START TRANSACTION" in runner.script and "COMMIT" in runner.script
    assert "DELETE FROM `wp_options`" in runner.script
    assert "INSERT INTO `wp_options`" in runner.script
    assert reference.options[0].value.hex() in runner.script
    assert reference.options[0].value.hex() not in " ".join(runner.calls[-1])
    assert runner.calls[-1][-1] == "site"


def test_mysql_never_restores_widgets_in_production() -> None:
    production = database().model_copy(update={"environment": Environment.PRODUCTION})
    runner = FakeCommandRunner()
    with pytest.raises(UnsafeOperationError, match="fora de TESTE"):
        MySQLAdapter({"db": production}, Secrets(), runner).restore_widgets(
            "db", "site", WidgetSnapshot.from_options([]), "run-1"
        )
    assert runner.calls == []


def test_wpcli_adapter_dry_run_multisite_and_failure() -> None:
    runner = FakeCommandRunner([FakeCommandResult(stdout="7\n")])
    adapter = WPCLIAdapter(runner)
    assert (
        adapter.search_replace(
            Path("/site"), "https://old", "https://new", dry_run=True, multisite=True, run_id="r"
        )
        == 7
    )
    assert "--dry-run" in runner.calls[0] and "--network" in runner.calls[0]
    assert "--precise" in runner.calls[0] and "--format=count" in runner.calls[0]
    with pytest.raises(Exception, match="failed"):
        WPCLIAdapter(FakeCommandRunner([FakeCommandResult(1, stderr="failed")])).update(
            Path("/site"), ["core", "update"], "r"
        )


def test_wpcli_reads_site_url_without_loading_plugins_or_themes() -> None:
    runner = FakeCommandRunner([FakeCommandResult(stdout="https://boletin.bireme.org\n")])
    assert WPCLIAdapter(runner).get_site_url(Path("/site"), "r") == ("https://boletin.bireme.org")
    assert "--skip-plugins" in runner.calls[0]
    assert "--skip-themes" in runner.calls[0]


def test_wpcli_detects_multisite_from_wordpress_constant() -> None:
    enabled = FakeCommandRunner([FakeCommandResult(stdout="true\n")])
    disabled = FakeCommandRunner([FakeCommandResult(return_code=1)])
    assert WPCLIAdapter(enabled).is_multisite(Path("/site"), "r") is True
    assert WPCLIAdapter(disabled).is_multisite(Path("/site"), "r") is False


def test_wpcli_search_replace_failure_does_not_expose_stderr() -> None:
    adapter = WPCLIAdapter(
        FakeCommandRunner([FakeCommandResult(return_code=1, stderr="database-password")])
    )
    with pytest.raises(Exception) as failure:
        adapter.search_replace(
            Path("/site"),
            "https://old",
            "https://new",
            dry_run=False,
            multisite=False,
            run_id="r",
        )
    assert "database-password" not in str(failure.value)


def test_runtime_search_replace_derives_test_url_from_discovered_site_url() -> None:
    class WordPress:
        replaced: tuple[str, str] | None = None

        def get_site_url(self, path: Path, run_id: str) -> str:
            del path, run_id
            return "https://boletin.bireme.org/wordpress"

        def is_multisite(self, path: Path, run_id: str) -> bool:
            del path, run_id
            return False

        def search_replace(self, path: Path, old_url: str, new_url: str, **kwargs: Any) -> int:
            del path, kwargs
            self.replaced = (old_url, new_url)
            return 4

    wordpress = WordPress()
    operations = RuntimeOperations(
        SimpleNamespace(),
        SimpleNamespace(),
        wordpress,  # type: ignore[arg-type]
        SimpleNamespace(),
    )
    pending = PendingOperation(
        PendingOperationType.SEARCH_REPLACE,
        {"source_domain": "bireme.org", "test_url": ""},
        "test",
    )
    context = {
        "run_id": "r",
        "installation": SimpleNamespace(
            destination_environment=Environment.TEST,
            destination_path=Path("/site"),
        ),
        "installations": {},
        "migration_plan": SimpleNamespace(pending_operations=(pending,)),
        "planned_step": PlannedStep("pending_search_replace", True, True, "", "", "site"),
    }

    result = operations.execute("pending_search_replace", context)

    assert result.status is StepStatus.SUCCEEDED
    assert wordpress.replaced == (
        "https://boletin.bireme.org/wordpress",
        "https://boletin.teste.bireme.org/wordpress",
    )


def test_wp_config_writer_updates_database_constants(tmp_path: Path) -> None:
    config = tmp_path / "wp-config.php"
    config.write_text(
        "\n".join(
            [
                "define('DB_HOST', 'old-host');",
                "define('DB_NAME', 'old-db');",
                "define('DB_USER', 'old-user');",
                "define('DB_PASSWORD', 'old-password');",
                "",
            ]
        ),
        encoding="utf-8",
    )

    WordPressConfigWriter().set_config(
        tmp_path,
        {
            "DB_HOST": "basalto21.bireme.br",
            "DB_NAME": "wp_decsfinder_tst",
            "DB_USER": "wordpress",
            "DB_PASSWORD": "secret-value",
        },
        "run-1",
    )

    updated = config.read_text(encoding="utf-8")

    assert "define('DB_HOST', 'basalto21.bireme.br');" in updated
    assert "define('DB_NAME', 'wp_decsfinder_tst');" in updated
    assert "define('DB_USER', 'wordpress');" in updated
    assert "define('DB_PASSWORD', 'secret-value');" in updated


def test_wp_config_writer_rejects_ambiguous_definition(tmp_path: Path) -> None:
    config = tmp_path / "wp-config.php"
    config.write_text(
        "\n".join(
            [
                "define('DB_HOST', 'one');",
                "define('DB_HOST', 'two');",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(WordPressUnavailableError):
        WordPressConfigWriter().set_config(
            tmp_path,
            {"DB_HOST": "basalto21.bireme.br"},
            "run-1",
        )


def test_wp_config_writer_rejects_unauthorized_constant(tmp_path: Path) -> None:
    config = tmp_path / "wp-config.php"
    config.write_text("define('WP_DEBUG', false);\n", encoding="utf-8")

    with pytest.raises(WordPressUnavailableError):
        WordPressConfigWriter().set_config(
            tmp_path,
            {"WP_DEBUG": "true"},
            "run-1",
        )


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


def test_key_ssh_reads_remote_config_without_wpcli_or_credentials_in_argv() -> None:
    key = password_server().model_copy(update={"authentication": "key", "password_secret": None})
    runner = FakeCommandRunner(
        [
            FakeCommandResult(
                stdout=(
                    "<?php\n"
                    'define("DB_NAME", "wp_portal_prod");\n'
                    "define('DB_HOST', 'prod-db:3307');\n"
                    "define('DB_USER', 'prod-user');\n"
                    "define('DB_PASSWORD', 'prod-password');\n"
                    "$table_prefix = 'wp_';\n"
                )
            )
        ]
    )
    adapter = RSyncSSHAdapter({"source": key}, Secrets(), runner)

    value = adapter.inspect_config("source", Path("/source/htdocs"), "run-1")

    assert value.database_name == "wp_portal_prod"
    assert value.database_host == "prod-db:3307"
    assert value.table_prefix == "wp_"
    assert runner.calls[0][0] == "ssh"
    assert "cat -- /source/htdocs/wp-config.php" in runner.calls[0][-1]
    assert "wp " not in runner.calls[0][-1]
    assert "password" not in " ".join(runner.calls[0])
    assert "user" not in " ".join(runner.calls[0])


def test_password_sftp_reads_remote_config_via_verified_session() -> None:
    client = FakeSSHClient()
    adapter = PasswordSFTPAdapter(
        {"source": password_server()}, Secrets(), client_factory=lambda: client
    )

    value = adapter.inspect_config("source", Path("/source/htdocs"), "run-1")

    assert value.database_host == "prod-db:3307"
    assert client.loaded_system_keys
    assert client.connect_kwargs["password"] == "password"
    assert client.exec_calls == []
    assert client.sftp.closed


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


@pytest.mark.parametrize("operation", ["probe", "dump", "siteurl"])
@pytest.mark.parametrize("failure", [False, True])
def test_source_mysql_credentials_are_private_and_temporary(tmp_path, operation, failure) -> None:
    from wp_modernizer.domain.models import SourceDatabaseConfiguration, SourceDatabaseConnection

    connection = SourceDatabaseConnection(
        "prod-db", 6612, "wp_prod", "private-user", "private-pass", "wp_"
    )
    config = SourceDatabaseConfiguration(
        "wp_prod", "prod-db", "private-user", "private-pass", "wp_"
    )
    assert "private-user" not in repr(connection) + repr(config)
    assert "private-pass" not in repr(connection) + repr(config)
    paths = []

    class Runner:
        def run(self, argv, **kwargs):
            assert "private-user" not in repr(argv)
            assert "private-pass" not in repr(argv)
            defaults = Path(argv[1].split("=", 1)[1])
            paths.append(defaults)
            assert stat.S_IMODE(defaults.stat().st_mode) == 0o600
            assert 'user="private-user"' in defaults.read_text()
            assert 'password="private-pass"' in defaults.read_text()
            if failure:
                raise InfrastructureError("private-user private-pass")
            return FakeCommandResult(stdout="https://portal.example.org\n")

    adapter = MySQLAdapter({}, Secrets(), Runner())
    if operation == "probe":
        result = adapter.probe_source(connection)
        assert result.status is (
            DatabaseAvailabilityStatus.UNKNOWN if failure else DatabaseAvailabilityStatus.AVAILABLE
        )
        assert "private-user" not in repr(result)
        assert "private-pass" not in repr(result)
    else:

        def invoke():
            if operation == "dump":
                adapter.dump_source(connection, tmp_path / "dump.sql", "run")
            else:
                adapter.read_source_site_url(connection)

        if failure:
            import traceback

            with pytest.raises(InfrastructureError) as raised:
                invoke()
            rendered = "".join(traceback.format_exception(raised.type, raised.value, raised.tb))
            assert "private-user private-pass" not in str(raised.value)
            assert "InfrastructureError: private-user private-pass" not in rendered
        else:
            invoke()
    assert paths
    assert all(not path.exists() for path in paths)


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (
            "ERROR 1045 access denied for private-user",
            DatabaseAvailabilityStatus.AUTHENTICATION_DENIED,
        ),
        ("ERROR 1049 unknown database", DatabaseAvailabilityStatus.SCHEMA_NOT_FOUND),
        ("ERROR 2002 can't connect", DatabaseAvailabilityStatus.ENDPOINT_UNAVAILABLE),
        ("ERROR 2003 connection refused", DatabaseAvailabilityStatus.ENDPOINT_UNAVAILABLE),
        ("ERROR 2005 unknown mysql server host", DatabaseAvailabilityStatus.ENDPOINT_UNAVAILABLE),
        ("ERROR 2006 server gone away", DatabaseAvailabilityStatus.ENDPOINT_UNAVAILABLE),
        ("ERROR 2013 lost connection", DatabaseAvailabilityStatus.ENDPOINT_UNAVAILABLE),
        ("private-user private-pass", DatabaseAvailabilityStatus.UNKNOWN),
    ],
)
def test_source_probe_classifies_errors_without_returning_server_output(stderr, expected):
    from wp_modernizer.domain.models import SourceDatabaseConnection

    connection = SourceDatabaseConnection("host", 6612, "db", "private-user", "private-pass", "wp_")
    adapter = MySQLAdapter(
        {},
        Secrets(),
        FakeCommandRunner(
            [
                FakeCommandResult(return_code=1, stderr=stderr),
            ]
        ),
    )
    result = adapter.probe_source(connection)
    assert result.status is expected
    assert "private-user" not in repr(result)
    assert "private-pass" not in repr(result)
