"""Local component integration; no SSH, MySQL, PHP or WordPress is required.

RuntimeOperations, PipelineRunner and LocalFileSystem execute together. Only
external ports, diagnostics and state persistence use test doubles.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.fakes.core import FakeClock, FakeProbe, FakeStateStore, health
from wp_modernizer.domain.enums import Environment, HealthStatus, Operation, RunStatus, StepStatus
from wp_modernizer.domain.models import PlannedStep, RunManifest
from wp_modernizer.domain.path_parser import InstallationPathParser
from wp_modernizer.infrastructure.filesystem import LocalFileSystem
from wp_modernizer.infrastructure.runtime_operations import RuntimeOperations
from wp_modernizer.pipeline.runner import PipelineRunner
from wp_modernizer.pipeline.steps import OperationStep


class LocalTransfer:
    def __init__(self):
        self.calls = []

    def get_server(self, server_id):
        return SimpleNamespace(environment=Environment.PRODUCTION)

    def copy_from(self, server_id, source, destination_parent, excludes, run_id):
        destination = destination_parent / source.name
        self.calls.append(destination)
        destination.mkdir(parents=True)
        (destination / "copied.txt").write_text("transferred", encoding="utf-8")
        return 1


def run_pipeline(tmp_path, probe, files, wordpress, names):
    destination = tmp_path / "example.org/wp-parent/htdocs/guides"
    installation = SimpleNamespace(
        source_server="production",
        source_environment=Environment.PRODUCTION,
        source_path=Path("/remote/example.org/wp-parent/htdocs/guides"),
        destination_environment=Environment.TEST,
        destination_path=destination,
        effective_destination_path=destination,
    )
    filesystem = LocalFileSystem()
    operations = RuntimeOperations(
        files,
        Mock(),
        wordpress,
        InstallationPathParser([tmp_path]),
        filesystem=filesystem,
    )
    steps = [
        OperationStep(PlannedStep(name, True, True, "", "", "guides"), operations) for name in names
    ]
    state = FakeStateStore()
    manifest = RunManifest("run", "guides", Operation.MIGRATE, RunStatus.RUNNING, "now", False)
    result = PipelineRunner(probe, state, filesystem, FakeClock()).run(
        manifest,
        destination,
        steps,
        {
            "run_id": "run",
            "installation": installation,
            "installations": {"guides": installation},
            "replace_existing": True,
            "recovery_data": {},
        },
    )
    return result, state, destination


@pytest.mark.parametrize("healthy_after_transfer", [True, False])
def test_absent_nested_destination_reaches_transfer_and_keeps_later_health_checks(
    tmp_path, healthy_after_transfer
):
    destination = tmp_path / "example.org/wp-parent/htdocs/guides"
    assert not destination.exists()
    files = LocalTransfer()
    wordpress = Mock()
    final_health = HealthStatus.HEALTHY if healthy_after_transfer else HealthStatus.PHP_CONFIG_ERROR

    probe_requirements = []

    class TransferProbe(FakeProbe):
        def probe(self, path, required_capabilities=None):
            probe_requirements.append(required_capabilities)
            # The destination remains absent until the real copy_files operation.
            if len(self.calls) < 2:
                assert not path.exists()
            else:
                assert (path / "copied.txt").read_text() == "transferred"
            return super().probe(path, required_capabilities)

    probe = TransferProbe(
        [
            health(HealthStatus.HEALTHY),
            health(HealthStatus.PHP_CONFIG_ERROR),
            health(HealthStatus.PHP_CONFIG_ERROR),
            health(final_health),
        ]
    )
    result, state, destination = run_pipeline(
        tmp_path,
        probe,
        files,
        wordpress,
        ["backup_existing_test", "copy_files", "preflight", "theme_update"],
    )

    backup, transfer = result.steps[:2]
    assert backup.status is StepStatus.EXECUTED
    assert backup.changed is False
    assert backup.metrics == {"destination_absent": 1}
    assert transfer.status is StepStatus.EXECUTED
    assert files.calls == [destination]
    # None asks for normal bootstrap diagnostics; set() explicitly suppresses them.
    assert probe.calls[:4] == [destination] * 4
    assert probe_requirements[:4] == [None, set(), set(), None]
    if healthy_after_transfer:
        assert result.status is RunStatus.EXECUTED
        wordpress.update.assert_called_once_with(destination, ("theme", "update", "--all"), "run")
        assert state.checkpoints == [
            "backup_existing_test",
            "copy_files",
            "preflight",
            "theme_update",
        ]
    else:
        assert result.status is RunStatus.UPDATE_FAILED_PRESERVED
        assert result.failed_step == "preflight"
        assert result.health_after is HealthStatus.PHP_CONFIG_ERROR
        assert state.checkpoints == ["backup_existing_test", "copy_files", "preflight"]
        wordpress.update.assert_not_called()


def test_real_backup_storage_failure_stops_pipeline_before_transfer(tmp_path):
    destination = tmp_path / "example.org/wp-parent/htdocs/guides"
    destination.mkdir(parents=True)
    marker = destination / "original.txt"
    marker.write_text("preserve", encoding="utf-8")
    # A file in place of the backup directory causes a real filesystem failure.
    (tmp_path / "example.org/wp-parent/.wp-modernizer-backups").write_text("blocked")
    files = LocalTransfer()
    wordpress = Mock()
    probe = FakeProbe([health(HealthStatus.HEALTHY)])

    result, state, _ = run_pipeline(
        tmp_path, probe, files, wordpress, ["backup_existing_test", "copy_files"]
    )

    assert result.status is RunStatus.UPDATE_FAILED_PRESERVED
    assert result.failed_step == "backup_existing_test"
    assert len(result.steps) == 1
    assert result.steps[0].status is StepStatus.FAILED
    assert "backup falhou" in result.steps[0].message
    assert "destination_absent" not in result.steps[0].metrics
    assert marker.read_text() == "preserve"
    assert files.calls == []
    assert state.checkpoints == ["backup_existing_test"]
    wordpress.update.assert_not_called()
