from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fakes.core import FakeClock, FakeFileSystem, FakeProbe, FakeStateStore, health
from wp_modernizer.domain.enums import (
    DatabaseAvailabilityStatus,
    Environment,
    HealthStatus,
    Operation,
    RunStatus,
    StepCapability,
    StepStatus,
)
from wp_modernizer.domain.models import (
    DatabaseProbeResult,
    PlannedStep,
    RunManifest,
    SourceDatabaseConfiguration,
)
from wp_modernizer.domain.path_parser import InstallationPathParser
from wp_modernizer.infrastructure.runtime_operations import RuntimeOperations
from wp_modernizer.pipeline.runner import PipelineRunner
from wp_modernizer.pipeline.steps import OperationStep


class Databases:
    def __init__(self, port_statuses: dict[int, DatabaseAvailabilityStatus] | None = None) -> None:
        self.endpoints = {
            "test-a": SimpleNamespace(environment=Environment.TEST, host="test-db", port=3306),
            "test-b": SimpleNamespace(environment=Environment.TEST, host="test-db-2", port=3306),
        }
        self.schemas = {
            "test-a": {"wp_portal_tst", "legacy_override", "installation_override"},
            "test-b": set(),
        }
        self.port_statuses = port_statuses or {6612: DatabaseAvailabilityStatus.AVAILABLE}
        self.probed_ports: list[int] = []
        self.mutable_calls: list[tuple[str, str]] = []
        self.site_url = "https://portal.bireme.org"
        self.source_reads: list[tuple[str, int, str, str]] = []

    def get_database(self, endpoint_id: str):
        return self.endpoints[endpoint_id]

    def list_schemas(self, endpoint_id: str):
        return self.schemas[endpoint_id]

    def probe_source(self, connection):
        self.probed_ports.append(connection.port)
        status = self.port_statuses.get(
            connection.port, DatabaseAvailabilityStatus.ENDPOINT_UNAVAILABLE
        )
        return DatabaseProbeResult(status, "sanitized")

    def dump_source(self, connection, output, run_id):
        del output, run_id
        self.mutable_calls.append(("dump-source", f"{connection.host}:{connection.port}"))

    def import_dump(self, endpoint_id, database, source, run_id):
        del database, source, run_id
        self.mutable_calls.append(("import", endpoint_id))

    def read_source_site_url(self, connection):
        self.source_reads.append(
            (connection.host, connection.port, connection.database_name, connection.table_prefix)
        )
        return self.site_url


class SourceWordPress:
    def __init__(self, *, database: str = "wp_portal_prod", host: str = "prod-db") -> None:
        self.database = database
        self.host = host
        self.reads: list[tuple[str, Path]] = []

    def get_server(self, server_id: str):
        assert server_id == "production-wordpress"
        return SimpleNamespace(environment=Environment.PRODUCTION)

    def inspect_config(self, server_id: str, path: Path, run_id: str):
        del run_id
        self.reads.append((server_id, path))
        return SourceDatabaseConfiguration(
            self.database, self.host, "production-user", "production-password", "wp_"
        )


def installation(**updates):
    values = {
        "destination_environment": Environment.TEST,
        "destination_path": Path("/home/apps/example.org/wp-test/htdocs"),
        "source_server": "production-wordpress",
        "source_environment": Environment.PRODUCTION,
        "source_path": Path("/home/apps/bireme.org/wp-prod/htdocs"),
        "database_override": None,
        "database_aliases": [],
        "allowed_database_endpoints": ["test-a"],
        "test_url": None,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def resolve(databases=None, source=None, item=None, legacy_override=None, recovery=None):
    databases = databases or Databases()
    source = source or SourceWordPress()
    operations = RuntimeOperations(
        SimpleNamespace(),
        databases,  # type: ignore[arg-type]
        SimpleNamespace(),
        InstallationPathParser([Path("/home/apps")]),
        database_overrides={"site": legacy_override} if legacy_override else {},
        source_inspection=source,  # type: ignore[arg-type]
    )
    recovery = recovery if recovery is not None else {}
    current = item or installation()
    result = operations.execute(
        "snapshot_source_database",
        {
            "run_id": "run-1",
            "installation": current,
            "installations": {},
            "recovery_data": recovery,
            "planned_step": PlannedStep("snapshot_source_database", False, True, "", "", "site"),
        },
    )
    return result, recovery, databases, source


def test_remote_source_and_test_only_allowlist_are_resolved_conventionally() -> None:
    result, recovery, databases, source = resolve()
    assert result.status is StepStatus.SUCCEEDED
    assert source.reads == [("production-wordpress", Path("/home/apps/bireme.org/wp-prod/htdocs"))]
    assert databases.probed_ports == [6612]
    assert databases.source_reads == [("prod-db", 6612, "wp_portal_prod", "wp_")]
    assert recovery["site"] == {
        "source_database": "wp_portal_prod",
        "source_database_host": "prod-db",
        "source_database_port": "6612",
        "target_database_endpoint": "test-a",
        "target_database": "wp_portal_tst",
        "source_server": "production-wordpress",
        "source_path": "/home/apps/bireme.org/wp-prod/htdocs",
        "source_environment": "production",
        "source_url": "https://portal.bireme.org",
        "test_url": "https://portal.teste.bireme.org",
    }
    serialized = repr(recovery)
    assert "production-user" not in serialized
    assert "production-password" not in serialized
    assert databases.mutable_calls == []


def test_database_override_changes_only_the_test_target() -> None:
    result, recovery, _, _ = resolve(
        item=installation(database_override="installation_override"),
        legacy_override="legacy_override",
    )
    assert result.status is StepStatus.SUCCEEDED
    assert recovery["site"]["source_database"] == "wp_portal_prod"
    assert recovery["site"]["target_database"] == "installation_override"


def test_database_resolution_preserves_existing_backup_recovery_data() -> None:
    recovery = {"site": {"backup_path": "/safe/backup", "backup_fingerprint": "sha256"}}
    result, recovery, _, _ = resolve(recovery=recovery)
    assert result.status is StepStatus.SUCCEEDED
    assert recovery["site"]["backup_path"] == "/safe/backup"
    assert recovery["site"]["source_database_host"] == "prod-db"


def test_default_port_6612_succeeds_without_trying_3306() -> None:
    databases = Databases({6612: DatabaseAvailabilityStatus.AVAILABLE})
    result, _, databases, _ = resolve(databases=databases)
    assert result.status is StepStatus.SUCCEEDED
    assert databases.probed_ports == [6612]


def test_default_port_falls_back_from_6612_to_3306() -> None:
    databases = Databases(
        {
            6612: DatabaseAvailabilityStatus.ENDPOINT_UNAVAILABLE,
            3306: DatabaseAvailabilityStatus.AVAILABLE,
        }
    )
    result, recovery, databases, _ = resolve(databases=databases)
    assert result.status is StepStatus.SUCCEEDED
    assert databases.probed_ports == [6612, 3306]
    assert recovery["site"]["source_database_port"] == "3306"


def test_both_default_ports_fail_with_sanitized_error() -> None:
    databases = Databases({6612: DatabaseAvailabilityStatus.ENDPOINT_UNAVAILABLE})
    result, recovery, databases, _ = resolve(databases=databases)
    assert result.status is StepStatus.FAILED
    assert databases.probed_ports == [6612, 3306]
    assert "6612, 3306" in result.message
    assert "production-password" not in result.message
    assert recovery == {}


def test_explicit_port_is_respected_without_fallback() -> None:
    databases = Databases({3307: DatabaseAvailabilityStatus.AVAILABLE})
    result, recovery, databases, _ = resolve(
        databases=databases, source=SourceWordPress(host="prod-db:3307")
    )
    assert result.status is StepStatus.SUCCEEDED
    assert databases.probed_ports == [3307]
    assert recovery["site"]["source_database_port"] == "3307"


def test_source_discovery_never_enumerates_or_expands_test_allowlist() -> None:
    databases = Databases()
    result, _, databases, _ = resolve(databases=databases)
    assert result.status is StepStatus.SUCCEEDED
    assert set(databases.endpoints) == {"test-a", "test-b"}


def test_missing_target_fails() -> None:
    databases = Databases()
    databases.schemas["test-a"] = set()
    result, _, _, _ = resolve(databases=databases)
    assert result.status is StepStatus.FAILED
    assert "nenhum candidato exato" in result.message.lower()


def test_invalid_source_site_url_fails_before_any_mutation() -> None:
    databases = Databases()
    databases.site_url = "http://portal.bireme.org"
    result, _, databases, _ = resolve(databases=databases)
    assert result.status is StepStatus.FAILED
    assert "HTTPS" in result.message
    assert databases.mutable_calls == []


def test_ambiguous_target_fails() -> None:
    databases = Databases()
    databases.schemas["test-b"] = {"wp_portal_tst"}
    result, _, _, _ = resolve(
        databases=databases,
        item=installation(allowed_database_endpoints=["test-a", "test-b"]),
    )
    assert result.status is StepStatus.FAILED
    assert "AMBIGUOUS_DATABASE" in result.message


@pytest.mark.parametrize("db_host", ["host:/socket", "2001:db8::1", "host:70000"])
def test_unsupported_database_host_fails_closed(db_host: str) -> None:
    result, _, _, _ = resolve(source=SourceWordPress(host=db_host))
    assert result.status is StepStatus.FAILED
    assert "formato" in result.message or "porta" in result.message


def test_dry_run_inspects_remote_source_without_copying_files() -> None:
    class Files:
        def __init__(self):
            self.calls = []

        def get_server(self, server_id):
            return SimpleNamespace(environment=Environment.PRODUCTION)

        def copy_from(self, *args):
            self.calls.append(args)
            return 0

    files = Files()
    databases = Databases()
    source = SourceWordPress()
    operations = RuntimeOperations(
        files,  # type: ignore[arg-type]
        databases,  # type: ignore[arg-type]
        SimpleNamespace(),
        InstallationPathParser([Path("/home/apps")]),
        source_inspection=source,  # type: ignore[arg-type]
    )
    mutable_copy = PlannedStep(
        "copy_files",
        True,
        True,
        "",
        "",
        "site",
        capability=StepCapability.MUTABLE_WITHOUT_SAFE_DRY_RUN,
    )
    inspect_source = PlannedStep(
        "snapshot_source_database",
        False,
        True,
        "",
        "",
        "site",
        capability=StepCapability.READ_ONLY,
    )
    manifest = RunManifest("run-1", "site", Operation.PIPELINE, RunStatus.RUNNING, "now", True)
    recovery: dict[str, dict[str, str]] = {}
    result = PipelineRunner(
        FakeProbe([health(HealthStatus.HEALTHY)]), FakeStateStore(), FakeFileSystem(), FakeClock()
    ).run(
        manifest,
        Path("/home/apps/example.org/wp-test/htdocs"),
        [OperationStep(mutable_copy, operations), OperationStep(inspect_source, operations)],
        {
            "run_id": "run-1",
            "installation": installation(),
            "installations": {},
            "recovery_data": recovery,
        },
    )
    assert files.calls == []
    assert result.steps[0].status is StepStatus.PLANNED
    assert result.steps[1].status is StepStatus.VALIDATED
    assert recovery["site"]["source_path"] == "/home/apps/bireme.org/wp-prod/htdocs"


@pytest.mark.parametrize(
    "status",
    [
        DatabaseAvailabilityStatus.AUTHENTICATION_DENIED,
        DatabaseAvailabilityStatus.SCHEMA_NOT_FOUND,
        DatabaseAvailabilityStatus.CONFIGURATION_INSUFFICIENT,
        DatabaseAvailabilityStatus.UNKNOWN,
    ],
)
def test_non_connectivity_failures_never_try_another_service(status) -> None:
    databases = Databases({6612: status, 3306: DatabaseAvailabilityStatus.AVAILABLE})
    result, recovery, _, _ = resolve(databases=databases)
    assert result.status is StepStatus.FAILED
    assert databases.probed_ports == [6612]
    assert recovery == {}


def test_explicit_unavailable_port_does_not_fall_back() -> None:
    databases = Databases({6612: DatabaseAvailabilityStatus.AVAILABLE})
    result, _, _, _ = resolve(databases=databases, source=SourceWordPress(host="prod-db:3307"))
    assert result.status is StepStatus.FAILED
    assert databases.probed_ports == [3307]


@pytest.mark.parametrize("changed", [None, "database", "host", "port"])
def test_resume_rediscovers_credentials_and_rejects_changed_source_identity(changed) -> None:
    result, recovery, databases, _ = resolve()
    assert result.status is StepStatus.SUCCEEDED

    class RotatedSource(SourceWordPress):
        def inspect_config(self, server_id, path, run_id):
            old = super().inspect_config(server_id, path, run_id)
            return SourceDatabaseConfiguration(
                old.database_name, old.database_host, "rotated-user", "rotated-password", "wp_"
            )

    source = RotatedSource(
        database="other_prod" if changed == "database" else "wp_portal_prod",
        host="other-host"
        if changed == "host"
        else "prod-db:3306"
        if changed == "port"
        else "prod-db",
    )
    databases.port_statuses[3306] = DatabaseAvailabilityStatus.AVAILABLE
    operations = RuntimeOperations(
        SimpleNamespace(),
        databases,
        SimpleNamespace(),
        InstallationPathParser([Path("/home/apps")]),
        source_inspection=source,
    )
    result = operations.execute(
        "copy_database",
        {
            "run_id": "resumed-run",
            "installation": installation(),
            "recovery_data": recovery,
            "planned_step": PlannedStep("copy_database", True, True, "", "", "site"),
        },
    )
    assert len(source.reads) == 1
    if changed:
        assert result.status is StepStatus.FAILED
        assert "mudou desde o snapshot" in result.message
        assert databases.mutable_calls == []
    else:
        assert result.status is StepStatus.SUCCEEDED
        assert databases.mutable_calls == [("dump-source", "prod-db:6612"), ("import", "test-a")]
    assert "rotated-user" not in repr(recovery)
    assert "rotated-password" not in repr(recovery)


def test_discovered_source_credentials_never_enter_persisted_manifest(tmp_path):
    from wp_modernizer.infrastructure.state import JsonStateStore

    result, recovery, _, _ = resolve()
    manifest = RunManifest("run-1", "site", Operation.MIGRATE, RunStatus.RUNNING, "now", False)
    manifest.recovery_data = recovery
    manifest.steps.append(result)
    store = JsonStateStore(tmp_path)
    store.create_run(manifest)
    restored = store.load_manifest("site", "run-1")
    assert restored.recovery_data == recovery
    persisted = "\n".join(path.read_text() for path in tmp_path.rglob("*.json"))
    for secret in ("production-user", "production-password", "DB_USER", "DB_PASSWORD"):
        assert secret not in persisted
        assert secret not in repr(manifest)
