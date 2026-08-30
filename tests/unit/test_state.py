from pathlib import Path

from tests.fakes.core import health
from wp_modernizer.domain.enums import HealthStatus, Operation, RunStatus, StepStatus
from wp_modernizer.domain.models import RunManifest, StepResult
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
