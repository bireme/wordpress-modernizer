from pathlib import Path

import pytest

from tests.fakes.core import health
from wp_modernizer.domain.enums import (
    Environment,
    HealthStatus,
    ManagedPluginStatus,
    Operation,
    RunStatus,
    StepStatus,
)
from wp_modernizer.domain.errors import StateStoreUnavailableError
from wp_modernizer.domain.models import (
    ManagedPlugin,
    ManagedPluginChange,
    ManagedPluginChanges,
    ManagedPluginResult,
    RunManifest,
    StepResult,
)
from wp_modernizer.domain.path_parser import InstallationPathParser
from wp_modernizer.domain.planning import MigrationPlanner
from wp_modernizer.domain.widgets import WidgetOption, WidgetSnapshot
from wp_modernizer.infrastructure.state import JsonStateStore


def test_state_preflight_creates_directory_and_proves_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "missing" / "state"

    JsonStateStore(root).preflight()

    assert root.is_dir()
    assert list(root.iterdir()) == []


def test_state_preflight_rejects_non_directory_path(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.write_text("not a directory")

    with pytest.raises(StateStoreUnavailableError, match="state_directory não está acessível"):
        JsonStateStore(root).preflight()


def test_state_preflight_rejects_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "state"
    original = Path.write_text

    def deny_probe_write(path: Path, *args: object, **kwargs: object) -> int:
        if path.parent == root and path.name.startswith(".wp-modernizer-preflight-"):
            raise PermissionError("read-only")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", deny_probe_write)

    with pytest.raises(StateStoreUnavailableError, match="escrita e leitura"):
        JsonStateStore(root).preflight()


def test_state_preflight_rejects_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "state"
    original = Path.read_text

    def deny_probe_read(path: Path, *args: object, **kwargs: object) -> str:
        if path.parent == root and path.name.startswith(".wp-modernizer-preflight-"):
            raise PermissionError("unreadable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_probe_read)

    with pytest.raises(StateStoreUnavailableError, match="escrita e leitura"):
        JsonStateStore(root).preflight()


def test_state_round_trip_and_layout(tmp_path: Path) -> None:
    store = JsonStateStore(tmp_path)
    manifest = RunManifest("run-id", "site", Operation.PIPELINE, RunStatus.RUNNING, "now", False)
    manifest.steps.append(StepResult("preflight", StepStatus.SUCCEEDED, False, "ok"))
    manifest.health_before = HealthStatus.HEALTHY
    manifest.widget_snapshot = WidgetSnapshot.from_options(
        [WidgetOption("wp_options", "widget_text", b"serialized\x00value", "yes")]
    )
    manifest.widget_diff = [
        {
            "event_type": "WIDGET_OPTION_CHANGED",
            "table": "wp_options",
            "option_name": "widget_text",
        }
    ]
    manifest.managed_plugins = [
        ManagedPlugin(
            "managed",
            "https://example.invalid/repo.git",
            "main",
            "replace_from_git",
            "skip",
        )
    ]
    manifest.managed_plugin_results = [
        ManagedPluginResult(
            "managed",
            "https://example.invalid/repo.git",
            "main",
            "replace_from_git",
            "skip",
            ManagedPluginStatus.SKIPPED,
            False,
            "skip explícito",
        )
    ]
    replacement = ManagedPlugin(
        "managed",
        "https://example.invalid/repo.git",
        "stable",
        "replace_from_git",
        "skip",
    )
    manifest.managed_plugin_changes = ManagedPluginChanges(
        added=(replacement,),
        removed=(manifest.managed_plugins[0],),
        changed=(ManagedPluginChange(manifest.managed_plugins[0], replacement),),
    )
    manifest.configuration_snapshot = {"installations": {"site": {"path": "/site"}}}
    store.create_run(manifest)
    store.save_checkpoint("site", "run-id", manifest.steps[0], health(HealthStatus.HEALTHY))
    loaded = store.load_manifest("site", "run-id")
    assert loaded.operation is Operation.PIPELINE
    assert loaded.steps[0].status is StepStatus.SUCCEEDED
    assert loaded.widget_snapshot == manifest.widget_snapshot
    assert loaded.widget_diff == manifest.widget_diff
    assert loaded.managed_plugins == manifest.managed_plugins
    assert loaded.managed_plugin_results == manifest.managed_plugin_results
    assert loaded.managed_plugin_changes == manifest.managed_plugin_changes
    assert loaded.configuration_snapshot == manifest.configuration_snapshot
    assert (tmp_path / "site" / "runs" / "run-id" / "checkpoints").is_dir()


def test_migration_plan_round_trip_preserves_step_metadata(tmp_path: Path) -> None:
    parser = InstallationPathParser([Path("/home/apps")])
    parent = parser.parse("/home/apps/example.org/wp-test/htdocs", "parent", Environment.TEST)
    child = parser.parse("/home/apps/example.org/wp-test/htdocs/child", "child", Environment.TEST)
    plan = MigrationPlanner().build(
        "parent", Environment.PRODUCTION, "source", "database", [parent, child]
    )
    manifest = RunManifest(
        "run-id",
        "parent",
        Operation.MIGRATE,
        RunStatus.RUNNING,
        "now",
        False,
        planned_steps=list(plan.steps),
        migration_plan=plan,
        execution_parameters={"replace_existing": True, "restore_widgets": False},
        recovery_data={
            "parent": {
                "source_endpoint": "production",
                "source_database": "wordpress",
                "target_endpoint": "test",
                "target_database": "wordpress_test",
            }
        },
        original_run_id="run-id",
    )
    store = JsonStateStore(tmp_path)

    store.create_run(manifest)
    loaded = store.load_manifest("parent", "run-id")

    assert loaded.migration_plan == plan
    assert loaded.planned_steps == list(plan.steps)
    assert loaded.execution_parameters == manifest.execution_parameters
    assert loaded.recovery_data == manifest.recovery_data
    assert loaded.original_run_id == "run-id"
    parent_copy = next(
        step
        for step in loaded.planned_steps
        if step.installation_id == "parent" and step.name == "copy_files"
    )
    assert parent_copy.excludes == (
        child.path,
        Path("*.sql"),
        Path(".wp-modernizer"),
    )
