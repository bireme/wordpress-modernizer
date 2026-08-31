import pytest

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
from wp_modernizer.domain.errors import ResumeConsistencyError
from wp_modernizer.domain.models import PlannedStep, RunManifest


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


def test_nested_wordpress_exclusion_reaches_executor_exactly_as_planned() -> None:
    operations = FakeOperations()
    app = service(operations)
    public_plan = app.plan("parent")
    expected = next(
        step["excludes"]
        for step in public_plan["steps"]
        if step["installation_id"] == "parent" and step["name"] == "copy_files"
    )

    result = app.execute(Operation.MIGRATE, "parent", dry_run=False)

    copy_context = next(
        context
        for name, context in zip(operations.calls, operations.contexts, strict=True)
        if name == "copy_files" and context["planned_step"].installation_id == "parent"
    )
    received = copy_context["planned_step"]
    assert isinstance(received, PlannedStep)
    assert [str(item) for item in received.excludes] == expected
    assert "/home/apps/example.org/wp-test/htdocs/child" in expected
    assert result.planned_steps == list(result.migration_plan.steps)  # type: ignore[union-attr]


def test_different_steps_keep_their_own_parameters_and_metadata() -> None:
    operations = FakeOperations()
    service(operations).execute(Operation.MIGRATE, "parent", dry_run=False)
    planned = [context["planned_step"] for context in operations.contexts]
    parent_backup = next(
        step
        for step in planned
        if step.installation_id == "parent" and step.name == "backup_existing_test"
    )
    parent_copy = next(
        step for step in planned if step.installation_id == "parent" and step.name == "copy_files"
    )
    assert parent_backup.excludes == ()
    assert parent_copy.excludes
    assert parent_copy.partial_recovery != parent_backup.partial_recovery


def test_resume_skips_successful_steps() -> None:
    state = FakeStateStore()
    app = service(state=state)
    old = app.execute(Operation.UPDATE, "parent", dry_run=False)
    result = app.resume("parent", old.run_id, dry_run=True)
    assert result.steps == old.steps
    assert result.operation is Operation.UPDATE
    assert result.planned_steps == old.planned_steps


@pytest.mark.parametrize(
    ("operation", "failed_step"),
    [
        (Operation.MIGRATE, "snapshot_source_database"),
        (Operation.UPDATE, "core_update"),
        (Operation.PIPELINE, "core_update"),
    ],
)
def test_interrupted_operation_resumes_the_same_original_plan(
    operation: Operation, failed_step: str
) -> None:
    state = FakeStateStore()
    operations = FakeOperations(fail_at=failed_step)
    app = service(operations=operations, state=state)
    old = app.execute(
        operation,
        "parent",
        dry_run=False,
        replace_existing=True,
        restore_widgets=True,
    )
    assert old.status is RunStatus.UPDATE_FAILED_PRESERVED
    completed_calls = list(operations.calls[:-1])
    operations.calls.clear()
    operations.contexts.clear()
    operations.fail_at = None

    resumed = app.resume("parent", old.run_id, dry_run=False)

    assert resumed.status is RunStatus.SUCCEEDED
    assert resumed.operation is operation
    assert resumed.planned_steps == old.planned_steps
    assert resumed.execution_parameters == old.execution_parameters
    assert resumed.resume_source_failed_step == failed_step
    assert operations.calls[0] == failed_step
    assert not set(completed_calls).intersection(operations.calls[:1])
    assert operations.contexts[0]["replace_existing"] is True
    assert operations.contexts[0]["restore_widgets"] is True


def test_resume_after_copy_files_does_not_copy_files_again() -> None:
    state = FakeStateStore()
    operations = FakeOperations(fail_at="snapshot_source_database")
    app = service(operations=operations, state=state)
    old = app.execute(Operation.MIGRATE, "parent", dry_run=False)
    assert "copy_files" in operations.calls
    operations.calls.clear()
    operations.contexts.clear()
    operations.fail_at = None

    app.resume("parent", old.run_id, dry_run=False)

    # A child installation can still have its own pending copy, but the completed parent copy
    # is never replayed. The resumed call starts exactly at the failed parent snapshot.
    assert operations.calls[0] == "snapshot_source_database"
    parent_copy_contexts = [
        context
        for name, context in zip(operations.calls, operations.contexts, strict=True)
        if name == "copy_files" and context["planned_step"].installation_id == "parent"
    ]
    assert parent_copy_contexts == []


def test_old_incomplete_manifest_is_rejected_instead_of_becoming_update() -> None:
    state = FakeStateStore()
    old = RunManifest(
        "legacy",
        "parent",
        Operation.MIGRATE,
        RunStatus.UPDATE_FAILED_PRESERVED,
        "now",
        False,
        failed_step="copy_files",
    )
    state.manifests[("parent", "legacy")] = old

    with pytest.raises(ResumeConsistencyError, match=r"informação suficiente.*resume seguro"):
        service(state=state).resume("parent", "legacy", dry_run=False)
