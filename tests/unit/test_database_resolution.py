from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fakes.core import FakeClock, FakeFileSystem, FakeProbe, FakeStateStore, health
from wp_modernizer.domain.enums import (
    Environment,
    HealthStatus,
    Operation,
    RunStatus,
    StepCapability,
    StepStatus,
)
from wp_modernizer.domain.models import PlannedStep, RunManifest, SourceDatabaseConfiguration
from wp_modernizer.infrastructure.runtime_operations import RuntimeOperations
from wp_modernizer.pipeline.runner import PipelineRunner
from wp_modernizer.pipeline.steps import OperationStep


class Databases:
    def __init__(self) -> None:
        self.endpoints = {
            "production-a": SimpleNamespace(
                environment=Environment.PRODUCTION, host="prod-db", port=3306
            ),
            "production-b": SimpleNamespace(
                environment=Environment.PRODUCTION, host="other-db", port=3306
            ),
            "test-a": SimpleNamespace(environment=Environment.TEST, host="test-db", port=3306),
            "test-b": SimpleNamespace(environment=Environment.TEST, host="test-db-2", port=3306),
        }
        self.schemas = {
            "production-a": {"wp_portal_prod"},
            "production-b": set(),
            "test-a": {"wp_portal_tst", "legacy_override", "installation_override"},
            "test-b": set(),
        }
        self.mutable_calls: list[tuple[str, str]] = []
        self.site_url = "https://portal.bireme.org"
        self.site_url_reads: list[tuple[str, str, str]] = []

    def endpoint_ids(self):
        return tuple(self.endpoints)

    def get_database(self, endpoint_id: str):
        return self.endpoints[endpoint_id]

    def list_schemas(self, endpoint_id: str):
        return self.schemas[endpoint_id]

    def dump(self, endpoint_id, database, output, run_id):
        self.mutable_calls.append(("dump", endpoint_id))

    def import_dump(self, endpoint_id, database, source, run_id):
        self.mutable_calls.append(("import", endpoint_id))

    def read_site_url(self, endpoint_id, database, table_prefix):
        self.site_url_reads.append((endpoint_id, database, table_prefix))
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
        return SourceDatabaseConfiguration(self.database, self.host, "wp_")


def installation(**updates):
    values = {
        "destination_environment": Environment.TEST,
        "destination_path": Path("/home/apps/example.org/wp-test/htdocs"),
        "source_server": "production-wordpress",
        "source_environment": Environment.PRODUCTION,
        "source_path": Path("/remote/example.org/wp-prod/htdocs"),
        "source_database_endpoint": None,
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
        SimpleNamespace(),
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
    assert source.reads == [("production-wordpress", Path("/remote/example.org/wp-prod/htdocs"))]
    assert databases.site_url_reads == [("production-a", "wp_portal_prod", "wp_")]
    assert recovery["site"] == {
        "source_database_endpoint": "production-a",
        "source_database": "wp_portal_prod",
        "target_database_endpoint": "test-a",
        "target_database": "wp_portal_tst",
        "source_server": "production-wordpress",
        "source_path": "/remote/example.org/wp-prod/htdocs",
        "source_environment": "production",
        "source_url": "https://portal.bireme.org",
        "test_url": "https://portal.teste.bireme.org",
    }
    assert databases.mutable_calls == []


def test_database_override_changes_only_the_test_target() -> None:
    result, recovery, _, _ = resolve(
        item=installation(database_override="installation_override"),
        legacy_override="legacy_override",
    )
    assert result.status is StepStatus.SUCCEEDED
    assert recovery["site"]["source_database"] == "wp_portal_prod"
    assert recovery["site"]["source_database_endpoint"] == "production-a"
    assert recovery["site"]["target_database"] == "installation_override"


def test_database_resolution_preserves_existing_backup_recovery_data() -> None:
    recovery = {"site": {"backup_path": "/safe/backup", "backup_fingerprint": "sha256"}}
    result, recovery, _, _ = resolve(recovery=recovery)
    assert result.status is StepStatus.SUCCEEDED
    assert recovery["site"]["backup_path"] == "/safe/backup"
    assert recovery["site"]["source_database_endpoint"] == "production-a"


def test_explicit_source_endpoint_avoids_db_host_heuristics() -> None:
    result, recovery, _, source = resolve(
        item=installation(source_database_endpoint="production-a"),
        source=SourceWordPress(host="unmapped-host"),
    )
    assert result.status is StepStatus.SUCCEEDED
    assert recovery["site"]["source_database_endpoint"] == "production-a"
    assert len(source.reads) == 1


def test_missing_source_fails_without_mutation() -> None:
    databases = Databases()
    databases.schemas["production-a"] = set()
    result, _, databases, _ = resolve(databases=databases)
    assert result.status is StepStatus.FAILED
    assert "não existe" in result.message
    assert databases.mutable_calls == []


def test_ambiguous_source_fails() -> None:
    databases = Databases()
    databases.endpoints["production-b"].host = "prod-db"
    databases.schemas["production-b"] = {"wp_portal_prod"}
    result, _, _, _ = resolve(databases=databases)
    assert result.status is StepStatus.FAILED
    assert "mais de um endpoint" in result.message


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


@pytest.mark.parametrize("db_host", ["host:/socket", "2001:db8::1"])
def test_unsupported_or_ambiguous_db_host_requires_explicit_endpoint(db_host: str) -> None:
    result, _, _, _ = resolve(source=SourceWordPress(host=db_host))
    assert result.status is StepStatus.FAILED
    assert "source_database_endpoint" in result.message or "formato" in result.message


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
        SimpleNamespace(),
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
        FakeProbe([health(HealthStatus.HEALTHY)]),
        FakeStateStore(),
        FakeFileSystem(),
        FakeClock(),
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
    assert recovery["site"]["source_path"] == "/remote/example.org/wp-prod/htdocs"
