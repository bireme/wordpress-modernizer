from pathlib import Path
from types import SimpleNamespace

from wp_modernizer.domain.enums import Environment, StepStatus
from wp_modernizer.domain.models import PlannedStep
from wp_modernizer.infrastructure.runtime_operations import RuntimeOperations


class Databases:
    def __init__(self) -> None:
        self.endpoints = {
            "production": SimpleNamespace(environment=Environment.PRODUCTION),
            "test": SimpleNamespace(environment=Environment.TEST),
        }
        self.schemas = {
            "production": {"wp_portal_prod"},
            "test": {"wp_portal_tst", "legacy_override", "installation_override"},
        }

    def get_database(self, endpoint_id: str):
        return self.endpoints[endpoint_id]

    def list_schemas(self, endpoint_id: str):
        return self.schemas[endpoint_id]


class WordPress:
    def __init__(self) -> None:
        self.config_reads: list[str] = []

    def get_config(self, path: Path, name: str, run_id: str) -> str:
        del path, run_id
        self.config_reads.append(name)
        return "wp_portal_prod"


def test_installation_override_maps_only_target_and_source_is_discovered() -> None:
    databases = Databases()
    wordpress = WordPress()
    operations = RuntimeOperations(
        SimpleNamespace(),
        databases,  # type: ignore[arg-type]
        wordpress,  # type: ignore[arg-type]
        SimpleNamespace(),
        database_overrides={"site": "legacy_override"},
    )
    installation = SimpleNamespace(
        destination_environment=Environment.TEST,
        destination_path=Path("/test/htdocs"),
        database_override="installation_override",
        database_aliases=[],
        allowed_database_endpoints=["production", "test"],
        source_environment=Environment.PRODUCTION,
    )
    recovery: dict[str, dict[str, str]] = {}
    context = {
        "run_id": "run-1",
        "installation": installation,
        "installations": {},
        "recovery_data": recovery,
        "planned_step": PlannedStep("snapshot_source_database", True, True, "", "", "site"),
    }

    result = operations.execute("snapshot_source_database", context)

    assert result.status is StepStatus.SUCCEEDED
    assert wordpress.config_reads == ["DB_NAME"]
    assert recovery["site"] == {
        "source_endpoint": "production",
        "source_database": "wp_portal_prod",
        "target_endpoint": "test",
        "target_database": "installation_override",
    }
