from pathlib import Path

import pytest

from tests.fakes.core import FakeCommandResult, FakeCommandRunner
from wp_modernizer.config.models import DatabaseConfig, ServerConfig
from wp_modernizer.domain.enums import Environment
from wp_modernizer.domain.errors import AuthenticationError, ConfigurationError, InfrastructureError
from wp_modernizer.infrastructure.filesystem import LocalFileSystem
from wp_modernizer.infrastructure.mysql.adapter import MySQLAdapter
from wp_modernizer.infrastructure.secrets import EnvironmentSecretProvider
from wp_modernizer.infrastructure.ssh.adapter import RSyncSSHAdapter
from wp_modernizer.infrastructure.wpcli.adapter import WPCLIAdapter


class Secrets:
    def get(self, reference: str) -> str:
        return {"USER": "user", "PASS": "password"}[reference]


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


def test_ssh_is_key_first_and_password_adapter_is_refused() -> None:
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
    password = key.copy(update={"authentication": "password", "password_secret": "PASS"})
    with pytest.raises(ConfigurationError, match="SSH com senha"):
        RSyncSSHAdapter({"s": password}, Secrets(), runner).copy_from(
            "s", Path("/source"), Path("/target"), [], "r"
        )


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
