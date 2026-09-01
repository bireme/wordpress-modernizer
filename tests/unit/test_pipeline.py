from copy import deepcopy
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
from wp_modernizer.domain.enums import (
    HealthStatus,
    Operation,
    RunStatus,
    StepCapability,
    StepStatus,
)
from wp_modernizer.domain.errors import ResumeConsistencyError
from wp_modernizer.domain.models import PlannedStep, RunManifest, StepResult
from wp_modernizer.domain.widgets import WidgetOption, WidgetSnapshot
from wp_modernizer.pipeline.runner import PipelineRunner
from wp_modernizer.pipeline.steps import OperationStep


def manifest(dry_run=False):
    return RunManifest("run", "site", Operation.UPDATE, RunStatus.RUNNING, "now", dry_run)


def test_successful_pipeline_checkpoints_every_step() -> None:
    state = FakeStateStore()
    operations = FakeOperations()
    probe = FakeProbe([health(HealthStatus.HEALTHY)])
    runner = PipelineRunner(probe, state, FakeFileSystem(), FakeClock())
    result = runner.run(
        manifest(),
        Path("/site"),
        [OperationStep("one", operations), OperationStep("two", operations)],
        {},
    )
    assert result.status is RunStatus.SUCCEEDED
    assert result.last_successful_step == "two"
    assert result.health_after is HealthStatus.HEALTHY
    assert state.checkpoints == ["one", "two"]
    # One initial probe plus one post-step probe. The latter probe is the final
    # validation; there is no synthetic final-health-check step.
    assert probe.calls == [Path("/site"), Path("/site"), Path("/site")]


def test_step_recovery_state_is_persisted_before_the_next_mutation() -> None:
    reference = WidgetSnapshot.from_options(
        [WidgetOption("wp_options", "sidebars_widgets", b"reference", "yes")]
    )

    class RecordingState(FakeStateStore):
        def __init__(self) -> None:
            super().__init__()
            self.saved: list[RunManifest] = []

        def save_manifest(self, current: RunManifest) -> None:
            self.saved.append(deepcopy(current))
            super().save_manifest(current)

    class SnapshotOperations(FakeOperations):
        def execute(self, step_name, context):
            if step_name == "snapshot":
                context["manifest"].widget_snapshot = reference
            return super().execute(step_name, context)

    state = RecordingState()
    current = manifest()
    operations = SnapshotOperations(fail_at="core_update")
    PipelineRunner(
        FakeProbe([health(HealthStatus.HEALTHY)]), state, FakeFileSystem(), FakeClock()
    ).run(
        current,
        Path("/site"),
        [OperationStep("snapshot", operations), OperationStep("core_update", operations)],
        {"manifest": current},
    )

    assert state.saved[0].widget_snapshot == reference
    assert [step.name for step in state.saved[0].steps] == ["snapshot"]


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


def test_dry_run_executes_read_only_step_as_validation() -> None:
    operations = FakeOperations()
    planned = PlannedStep(
        "inspect",
        False,
        True,
        "",
        "",
        "site",
        capability=StepCapability.READ_ONLY,
    )
    result = PipelineRunner(
        FakeProbe([health(HealthStatus.HEALTHY)]),
        FakeStateStore(),
        FakeFileSystem(),
        FakeClock(),
    ).run(manifest(True), Path("/site"), [OperationStep(planned, operations)], {})

    assert operations.calls == ["inspect"]
    assert result.steps[0].status is StepStatus.VALIDATED
    assert result.status is RunStatus.VALIDATED


def test_dry_run_uses_separate_native_validation_entrypoint() -> None:
    operations = FakeOperations()
    planned = PlannedStep(
        "native",
        True,
        True,
        "",
        "",
        "site",
        capability=StepCapability.MUTABLE_WITH_NATIVE_DRY_RUN,
    )
    result = PipelineRunner(
        FakeProbe([health(HealthStatus.HEALTHY)]),
        FakeStateStore(),
        FakeFileSystem(),
        FakeClock(),
    ).run(manifest(True), Path("/site"), [OperationStep(planned, operations)], {})

    assert operations.calls == []
    assert operations.validation_calls == ["native"]
    assert result.steps[0].status is StepStatus.VALIDATED


def test_dry_run_rejects_native_validation_that_claims_mutation() -> None:
    class UnsafeValidation(FakeOperations):
        def validate(self, step_name, context):
            return StepResult(step_name, StepStatus.VALIDATED, True, "unsafe")

    operations = UnsafeValidation()
    planned = PlannedStep(
        "native",
        True,
        True,
        "",
        "",
        "site",
        capability=StepCapability.MUTABLE_WITH_NATIVE_DRY_RUN,
    )

    with pytest.raises(RuntimeError, match="mutação em dry-run"):
        PipelineRunner(
            FakeProbe([health(HealthStatus.HEALTHY)]),
            FakeStateStore(),
            FakeFileSystem(),
            FakeClock(),
        ).run(manifest(True), Path("/site"), [OperationStep(planned, operations)], {})


def test_resume_detects_manual_intervention() -> None:
    filesystem = FakeFileSystem(fingerprint="changed")
    runner = PipelineRunner(
        FakeProbe([health(HealthStatus.HEALTHY)]), FakeStateStore(), filesystem, FakeClock()
    )
    old = manifest()
    old.filesystem_fingerprint = "before"
    with pytest.raises(ResumeConsistencyError, match="Intervenção manual"):
        runner.assert_resume_consistent(old, Path("/site"))
