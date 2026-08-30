from tests.fakes.core import (
    FakeClock,
    FakeFileSystem,
    FakeIds,
    FakeOperations,
    FakeProbe,
    FakeStateStore,
    health,
)
from wp_modernizer.application.service import ModernizerService
from wp_modernizer.config.models import ApplicationConfig
from wp_modernizer.domain.enums import HealthStatus, Operation, RunStatus


def config() -> ApplicationConfig:
    return ApplicationConfig.model_validate(
        {
            "state_directory": "state",
            "allowed_app_roots": ["/home/apps"],
            "servers": {
                "source": {
                    "host": "source.example.invalid",
                    "environment": "production",
                    "username_secret": "USER",
                }
            },
            "databases": {
                "db": {
                    "host": "db.example.invalid",
                    "username_secret": "DB_USER",
                    "password_secret": "DB_PASSWORD",
                }
            },
            "installations": {
                "parent": {
                    "source_server": "source",
                    "source_environment": "production",
                    "source_path": "/home/apps/example.org/wp-main/htdocs",
                    "destination_path": "/home/apps/example.org/wp-test/htdocs",
                    "destination_environment": "test",
                    "allowed_database_endpoints": ["db"],
                },
                "child": {
                    "source_server": "source",
                    "source_environment": "production",
                    "source_path": "/home/apps/example.org/wp-main/htdocs/child",
                    "destination_path": "/home/apps/example.org/wp-test/htdocs/child",
                    "destination_environment": "test",
                    "allowed_database_endpoints": ["db"],
                },
            },
        }
    )


def service(operations=None, state=None) -> ModernizerService:
    return ModernizerService(
        config(),
        FakeProbe([health(HealthStatus.HEALTHY)]),
        state or FakeStateStore(),
        FakeFileSystem(),
        FakeClock(),
        FakeIds(),
        operations or FakeOperations(),
    )


def test_diagnose_and_inventory_degrade_fields_independently() -> None:
    app = service()
    assert app.diagnose("parent")["health"] == "HEALTHY"
    inventory = app.inventory("parent")
    assert inventory["domain"] == "example.org"
    assert str(inventory["wordpress_version"]).startswith("indisponível")


def test_plan_includes_nested_child_and_pending_operation() -> None:
    plan = service().plan("parent")
    assert [item["installation_id"] for item in plan["installations"]] == ["parent", "child"]
    assert plan["pending_operations"][0]["operation_type"] == "SEARCH_REPLACE"


def test_pipeline_dry_run_calls_no_operations() -> None:
    operations = FakeOperations()
    result = service(operations).execute(Operation.PIPELINE, "parent", dry_run=True)
    assert result.status is RunStatus.PLANNED
    assert operations.calls == []
    assert len(result.steps) > 10


def test_update_executes_declared_pipeline() -> None:
    operations = FakeOperations()
    result = service(operations).execute(Operation.UPDATE, "parent", dry_run=False)
    assert result.status is RunStatus.SUCCEEDED
    assert operations.calls[0] == "preflight"
    assert operations.calls[-1] == "final_health_check"


def test_resume_skips_successful_steps() -> None:
    state = FakeStateStore()
    app = service(state=state)
    old = app.execute(Operation.UPDATE, "parent", dry_run=False)
    result = app.resume("parent", old.run_id, dry_run=True)
    assert result.steps == []
