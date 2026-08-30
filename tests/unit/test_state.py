from pathlib import Path

from tests.fakes.core import health
from wp_modernizer.domain.enums import Environment, HealthStatus, Operation, RunStatus, StepStatus
from wp_modernizer.domain.models import RunManifest, StepResult
from wp_modernizer.domain.path_parser import InstallationPathParser
from wp_modernizer.domain.planning import MigrationPlanner
from wp_modernizer.infrastructure.state import JsonStateStore


def test_state_round_trip_and_layout(tmp_path: Path) -> None:
    store = JsonStateStore(tmp_path)
    manifest = RunManifest("run-id", "site", Operation.PIPELINE, RunStatus.RUNNING, "now", False)
    manifest.steps.append(StepResult("preflight", StepStatus.SUCCEEDED, False, "ok"))
    manifest.health_before = HealthStatus.HEALTHY
    store.create_run(manifest)
    store.save_checkpoint("site", "run-id", manifest.steps[0], health(HealthStatus.HEALTHY))
    loaded = store.load_manifest("site", "run-id")
    assert loaded.operation is Operation.PIPELINE
    assert loaded.steps[0].status is StepStatus.SUCCEEDED
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
    )
    store = JsonStateStore(tmp_path)

    store.create_run(manifest)
    loaded = store.load_manifest("parent", "run-id")

    assert loaded.migration_plan == plan
    assert loaded.planned_steps == list(plan.steps)
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
