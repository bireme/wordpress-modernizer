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
from wp_modernizer.config.models import ApplicationConfig, ManagedPluginConfig
from wp_modernizer.domain.enums import HealthStatus, ManagedPluginStatus, Operation, RunStatus
from wp_modernizer.domain.errors import ResumeConsistencyError, StateStoreUnavailableError
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


class UnavailableStateStore(FakeStateStore):
    def preflight(self) -> None:
        super().preflight()
        raise StateStoreUnavailableError("state_directory indisponível")

    def load_manifest(self, installation_id: str, run_id: str) -> RunManifest:
        raise AssertionError("resume não deve ler estado após falha do preflight")


@pytest.mark.parametrize("command", ["execute", "resume"])
def test_mutable_operation_stops_before_probe_or_state_load_when_store_is_unavailable(
    command: str,
) -> None:
    state = UnavailableStateStore()
    probe = FakeProbe([health(HealthStatus.HEALTHY)])
    operations = FakeOperations()
    app = ModernizerService(
        config(), probe, state, FakeFileSystem(), FakeClock(), FakeIds(), operations
    )

    with pytest.raises(StateStoreUnavailableError):
        if command == "execute":
            app.execute(Operation.UPDATE, "parent", dry_run=False)
        else:
            app.resume("parent", "old-run", dry_run=False)

    assert state.preflight_calls == 1
    assert probe.calls == []
    assert operations.calls == []


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
    assert plan["steps"][-1]["name"] == "pending_search_replace"


def test_pipeline_does_not_execute_pending_search_replace_twice() -> None:
    plan = service().execute(Operation.PIPELINE, "parent", dry_run=True)
    assert [step.name for step in plan.planned_steps].count("pending_search_replace") == 1


def test_pipeline_dry_run_calls_only_read_and_native_validation_operations() -> None:
    operations = FakeOperations()
    result = service(operations).execute(Operation.PIPELINE, "parent", dry_run=True)
    assert result.status is RunStatus.PLANNED
    assert operations.calls == ["preflight"]
    assert operations.validation_calls == []
    assert len(result.steps) > 10


def test_update_dry_run_records_managed_plugin_plan_without_mutating_operations() -> None:
    operations = FakeOperations()
    app = service(operations)
    app.config.managed_plugins = [
        ManagedPluginConfig(
            slug="managed",
            repository="https://example.invalid/plugin.git",
            branch="stable",
            strategy="replace_from_git",
            dirty_policy="skip",
        )
    ]

    result = app.execute(Operation.UPDATE, "parent", dry_run=True)

    assert operations.calls == ["preflight"]
    assert operations.validation_calls == []
    assert result.managed_plugins[0].branch == "stable"
    assert result.managed_plugin_results[0].status is ManagedPluginStatus.PLANNED
    assert result.managed_plugin_results[0].dirty_policy == "skip"


def test_update_executes_declared_pipeline() -> None:
    operations = FakeOperations()
    result = service(operations).execute(Operation.UPDATE, "parent", dry_run=False)
    assert result.status is RunStatus.SUCCEEDED
    assert operations.calls[0] == "preflight"
    assert operations.calls[-1] == "widget_validation"
    assert "final_health_check" not in operations.calls
    assert "final_health_check" not in [step.name for step in result.planned_steps]
    assert "final_health_check" not in [step.name for step in result.steps]
    snapshot_index = operations.calls.index("snapshot")
    for update_step in (
        "core_update",
        "core_database_update",
        "third_party_plugin_update",
        "theme_update",
    ):
        assert snapshot_index < operations.calls.index(update_step)


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

    resumed = app.resume("parent", old.run_id, dry_run=False, restore_widgets=True)

    assert resumed.status is RunStatus.SUCCEEDED
    assert resumed.operation is operation
    assert resumed.planned_steps == old.planned_steps
    assert resumed.execution_parameters == old.execution_parameters
    assert resumed.resume_source_failed_step == failed_step
    assert operations.calls[0] == failed_step
    assert not set(completed_calls).intersection(operations.calls[:1])
    assert operations.contexts[0]["replace_existing"] is True
    assert operations.contexts[0]["restore_widgets"] is True


def test_resume_does_not_reuse_previous_widget_restore_authorization() -> None:
    state = FakeStateStore()
    operations = FakeOperations(fail_at="core_update")
    app = service(operations=operations, state=state)
    old = app.execute(
        Operation.UPDATE,
        "parent",
        dry_run=False,
        restore_widgets=True,
    )
    operations.contexts.clear()
    operations.calls.clear()
    operations.fail_at = None

    resumed = app.resume("parent", old.run_id, dry_run=False)

    assert resumed.execution_parameters == {
        "replace_existing": False,
        "restore_widgets": False,
    }
    assert operations.contexts[0]["restore_widgets"] is False


def test_resume_from_managed_plugin_refresh_reconciles_current_plugins() -> None:
    state = FakeStateStore()
    operations = FakeOperations(fail_at="managed_plugin_refresh")
    app = service(operations=operations, state=state)
    app.config.managed_plugins = [
        ManagedPluginConfig(
            slug="polylang",
            repository="https://example.invalid/polylang.git",
            branch="main",
            dirty_policy="abort",
        ),
        ManagedPluginConfig(
            slug="kept",
            repository="https://example.invalid/kept.git",
            branch="main",
            dirty_policy="abort",
        ),
    ]
    original = app.execute(Operation.UPDATE, "parent", dry_run=False)
    assert original.failed_step == "managed_plugin_refresh"

    app.config.managed_plugins = [
        ManagedPluginConfig(
            slug="kept",
            repository="https://example.invalid/kept.git",
            branch="stable",
            dirty_policy="skip",
        ),
        ManagedPluginConfig(
            slug="added",
            repository="https://example.invalid/added.git",
            branch="main",
            dirty_policy="abort",
        ),
    ]
    operations.calls.clear()
    operations.contexts.clear()
    operations.fail_at = None

    resumed = app.resume("parent", original.run_id, dry_run=False)

    assert resumed.status is RunStatus.SUCCEEDED
    assert [plugin.slug for plugin in resumed.managed_plugins] == ["kept", "added"]
    assert operations.calls[:2] == ["managed_plugin_refresh", "third_party_plugin_update"]
    assert resumed.managed_plugin_changes is not None
    assert [plugin.slug for plugin in resumed.managed_plugin_changes.removed] == ["polylang"]
    assert [plugin.slug for plugin in resumed.managed_plugin_changes.added] == ["added"]
    assert [change.before.slug for change in resumed.managed_plugin_changes.changed] == ["kept"]
    assert resumed.managed_plugin_changes.changed[0].before.branch == "main"
    assert resumed.managed_plugin_changes.changed[0].after.branch == "stable"


def test_resume_rejects_other_critical_configuration_changes() -> None:
    state = FakeStateStore()
    operations = FakeOperations(fail_at="managed_plugin_refresh")
    app = service(operations=operations, state=state)
    app.config.managed_plugins = [
        ManagedPluginConfig(
            slug="polylang",
            repository="https://example.invalid/polylang.git",
        )
    ]
    original = app.execute(Operation.UPDATE, "parent", dry_run=False)
    original_plan = list(original.planned_steps)
    original_parameters = dict(original.execution_parameters or {})
    app.config.managed_plugins = []
    app.config.installations["parent"].database_override = "different_database"

    with pytest.raises(ResumeConsistencyError, match="configuração crítica diverge"):
        app.resume("parent", original.run_id, dry_run=False)

    assert original.planned_steps == original_plan
    assert original.execution_parameters == original_parameters
    assert operations.calls[-1] == "managed_plugin_refresh"


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
