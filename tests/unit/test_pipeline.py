from pathlib import Path

import pytest

from tests.fakes.core import (
    FakeClock,
    FakeFileSystem,
    FakeOperations,
    FakeProbe,
    FakeStateStore,
    health,
)
from wp_modernizer.domain.enums import HealthStatus, Operation, RunStatus
from wp_modernizer.domain.errors import ResumeConsistencyError
from wp_modernizer.domain.models import RunManifest
from wp_modernizer.pipeline.runner import PipelineRunner
from wp_modernizer.pipeline.steps import OperationStep


def manifest(dry_run=False):
    return RunManifest("run", "site", Operation.UPDATE, RunStatus.RUNNING, "now", dry_run)


def test_successful_pipeline_checkpoints_every_step() -> None:
    state = FakeStateStore()
    operations = FakeOperations()
    runner = PipelineRunner(
        FakeProbe([health(HealthStatus.HEALTHY)]), state, FakeFileSystem(), FakeClock()
    )
    result = runner.run(
        manifest(),
        Path("/site"),
        [OperationStep("one", operations), OperationStep("two", operations)],
        {},
    )
    assert result.status is RunStatus.SUCCEEDED
    assert result.last_successful_step == "two"
    assert state.checkpoints == ["one", "two"]


@pytest.mark.parametrize("failed", ["one", "two", "three"])
def test_failure_in_each_step_stops_and_preserves(failed: str) -> None:
    operations = FakeOperations(fail_at=failed)
    runner = PipelineRunner(
        FakeProbe([health(HealthStatus.HEALTHY)]), FakeStateStore(), FakeFileSystem(), FakeClock()
    )
    names = ["one", "two", "three"]
    result = runner.run(
        manifest(), Path("/site"), [OperationStep(name, operations) for name in names], {}
    )
    assert result.status is RunStatus.UPDATE_FAILED_PRESERVED
    assert result.failed_step == failed
    assert operations.calls == names[: names.index(failed) + 1]


def test_health_regression_stops_even_when_command_succeeds() -> None:
    probe = FakeProbe([health(HealthStatus.HEALTHY), health(HealthStatus.WPCLI_PARTIAL)])
    result = PipelineRunner(probe, FakeStateStore(), FakeFileSystem(), FakeClock()).run(
        manifest(), Path("/site"), [OperationStep("core", FakeOperations())], {}
    )
    assert result.failed_step == "core"
    assert result.health_before is HealthStatus.HEALTHY
    assert result.health_after is HealthStatus.WPCLI_PARTIAL


def test_dry_run_never_calls_mutable_adapter() -> None:
    operations = FakeOperations()
    result = PipelineRunner(
        FakeProbe([health(HealthStatus.HEALTHY)]), FakeStateStore(), FakeFileSystem(), FakeClock()
    ).run(manifest(True), Path("/site"), [OperationStep("core", operations)], {})
    assert operations.calls == []
    assert result.status is RunStatus.PLANNED
    assert result.steps[0].message.startswith("dry-run")


def test_resume_detects_manual_intervention() -> None:
    filesystem = FakeFileSystem(fingerprint="changed")
    runner = PipelineRunner(
        FakeProbe([health(HealthStatus.HEALTHY)]), FakeStateStore(), filesystem, FakeClock()
    )
    old = manifest()
    old.filesystem_fingerprint = "before"
    with pytest.raises(ResumeConsistencyError, match="Intervenção manual"):
        runner.assert_resume_consistent(old, Path("/site"))
