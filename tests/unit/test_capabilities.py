from pathlib import Path

from tests.fakes.core import FakeCommandResult, FakeCommandRunner, FakeFileSystem
from wp_modernizer.diagnostics.capability import CapabilityProbe
from wp_modernizer.domain.enums import Capability, DatabaseAvailabilityStatus, HealthStatus
from wp_modernizer.domain.models import DatabaseProbeResult

PATH = Path("/site")


class Database:
    def __init__(self, status=DatabaseAvailabilityStatus.AVAILABLE):
        self.status = status
        self.calls = []

    def probe_database(self, endpoint_id, database):
        self.calls.append((endpoint_id, database))
        details = {
            DatabaseAvailabilityStatus.AVAILABLE: (
                "endpoint alcançável; autenticação aceita; schema disponível"
            ),
            DatabaseAvailabilityStatus.AUTHENTICATION_DENIED: (
                "endpoint alcançável; autenticação negada"
            ),
            DatabaseAvailabilityStatus.SCHEMA_NOT_FOUND: (
                "endpoint alcançável; autenticação aceita; schema inexistente"
            ),
            DatabaseAvailabilityStatus.ENDPOINT_UNAVAILABLE: "endpoint indisponível",
            DatabaseAvailabilityStatus.CONFIGURATION_INSUFFICIENT: "configuração insuficiente",
            DatabaseAvailabilityStatus.UNKNOWN: "estado do banco desconhecido",
        }
        return DatabaseProbeResult(self.status, details[self.status])


class WordPress:
    def __init__(self, database="wordpress_test"):
        self.database = database

    def get_config(self, path, name, run_id):
        del path, name, run_id
        if isinstance(self.database, Exception):
            raise self.database
        return self.database


def run_probe(
    codes,
    config="<?php define('MULTISITE', true);",
    database_status=DatabaseAvailabilityStatus.AVAILABLE,
    endpoints=("db",),
    database_name="wordpress_test",
):
    files = {PATH / "wp-config.php": config, PATH / "wp-includes" / "version.php": "version"}
    runner = FakeCommandRunner(
        [FakeCommandResult(code, "ok" if code == 0 else "Fatal error") for code in codes]
    )
    return CapabilityProbe(
        runner,
        FakeFileSystem(files),
        database=Database(database_status),
        wordpress=WordPress(database_name),
        database_endpoints={PATH: endpoints},
    ).probe(PATH)


def test_operational_database_is_healthy() -> None:
    report = run_probe([0, 0, 0, 0, 0, 0])
    assert report.health is HealthStatus.HEALTHY
    assert report.has(Capability.MULTISITE)
    assert report.has(Capability.DATABASE_AVAILABLE)


def test_php_lint_ok_with_mysql_offline_is_not_available() -> None:
    report = run_probe(
        [0, 0, 0, 0, 0, 0],
        database_status=DatabaseAvailabilityStatus.ENDPOINT_UNAVAILABLE,
    )
    database = next(
        item for item in report.results if item.capability is Capability.DATABASE_AVAILABLE
    )
    assert not database.available
    assert database.detail == "endpoint indisponível"
    assert report.health is HealthStatus.DATABASE_UNAVAILABLE


def test_php_lint_ok_with_access_denied_is_not_available() -> None:
    report = run_probe(
        [0, 0, 0, 0, 0, 0],
        database_status=DatabaseAvailabilityStatus.AUTHENTICATION_DENIED,
    )
    database = next(
        item for item in report.results if item.capability is Capability.DATABASE_AVAILABLE
    )
    assert not database.available
    assert database.detail == "endpoint alcançável; autenticação negada"


def test_missing_schema_is_distinct_from_unreachable_endpoint() -> None:
    report = run_probe(
        [0, 0, 0, 0, 0, 0],
        database_status=DatabaseAvailabilityStatus.SCHEMA_NOT_FOUND,
    )
    database = next(
        item for item in report.results if item.capability is Capability.DATABASE_AVAILABLE
    )
    assert not database.available
    assert database.detail.endswith("schema inexistente")


def test_insufficient_configuration_is_not_assumed_healthy() -> None:
    report = run_probe([0, 0, 0, 0, 0, 0], endpoints=())
    database = next(
        item for item in report.results if item.capability is Capability.DATABASE_AVAILABLE
    )
    assert not database.available
    assert database.detail == "configuração insuficiente"
    assert report.health is HealthStatus.DATABASE_UNAVAILABLE


def test_plugin_or_theme_fatal() -> None:
    report = run_probe([0, 0, 0, 0, 0, 1])
    assert report.health is HealthStatus.PLUGIN_OR_THEME_CONFLICT
    assert report.fatal_errors


def test_wpcli_partial() -> None:
    assert run_probe([0, 0, 0, 0, 1, 1]).health is HealthStatus.WPCLI_PARTIAL


def test_wpcli_absent() -> None:
    assert run_probe([0, 0, 1]).health is HealthStatus.DATABASE_UNAVAILABLE


def test_invalid_config() -> None:
    assert run_probe([0, 1, 0, 0, 0, 0]).health is HealthStatus.PHP_CONFIG_ERROR


def test_core_incomplete() -> None:
    files = {PATH / "wp-config.php": "<?php"}
    report = CapabilityProbe(
        FakeCommandRunner([FakeCommandResult() for _ in range(6)]),
        FakeFileSystem(files),
        database=Database(),
        wordpress=WordPress(),
        database_endpoints={PATH: ("db",)},
    ).probe(PATH)
    assert report.health is HealthStatus.CORE_INCOMPLETE
