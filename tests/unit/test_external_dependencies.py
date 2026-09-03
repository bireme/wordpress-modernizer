from pathlib import Path

import pytest

from tests.fakes.core import (
    FakeClock,
    FakeCommandResult,
    FakeCommandRunner,
    FakeExecutableLocator,
    FakeFileSystem,
    FakeOperations,
    FakeStateStore,
)
from wp_modernizer.application.dependencies import required_capabilities
from wp_modernizer.config.models import ApplicationConfig
from wp_modernizer.diagnostics.capability import CapabilityProbe
from wp_modernizer.domain.enums import Capability, Operation, RunStatus, StepCapability
from wp_modernizer.domain.errors import MissingCapabilityError
from wp_modernizer.domain.models import PlannedStep, RunManifest
from wp_modernizer.pipeline.runner import PipelineRunner
from wp_modernizer.pipeline.steps import OperationStep


def _config(*, authentication: str = "key", managed_plugins: bool = False) -> ApplicationConfig:
    server: dict[str, object] = {
        "host": "source.example.invalid",
        "environment": "production",
        "username_secret": "SSH_USER",
        "authentication": authentication,
    }
    if authentication == "password":
        server["password_secret"] = "SSH_PASSWORD"
    raw: dict[str, object] = {
        "allowed_app_roots": ["/home/apps"],
        "servers": {"source": server},
        "databases": {
            "db": {
                "host": "db.example.invalid",
                "username_secret": "DB_USER",
                "password_secret": "DB_PASSWORD",
            }
        },
        "installations": {
            "site": {
                "source_server": "source",
                "source_environment": "production",
                "source_path": "/home/apps/example.org/wp-main/htdocs",
                "destination_path": "/home/apps/example.org/wp-test/htdocs",
                "allowed_database_endpoints": ["db"],
            }
        },
    }
    if managed_plugins:
        raw["managed_plugins"] = [
            {"slug": "managed", "repository": "https://example.invalid/managed.git"}
        ]
    return ApplicationConfig.model_validate(raw)


def _step(name: str, capability: StepCapability) -> PlannedStep:
    return PlannedStep(
        name,
        capability is not StepCapability.READ_ONLY,
        True,
        "",
        "",
        "site",
        capability=capability,
    )


def test_real_migration_requires_key_transport_and_database_dump_tools() -> None:
    steps = (
        _step("copy_files", StepCapability.MUTABLE_WITHOUT_SAFE_DRY_RUN),
        _step("copy_database", StepCapability.MUTABLE_WITHOUT_SAFE_DRY_RUN),
    )

    required = required_capabilities(_config(), steps, dry_run=False)

    assert {Capability.SSH_AVAILABLE, Capability.RSYNC_AVAILABLE} <= required
    assert Capability.MYSQLDUMP_AVAILABLE in required
    assert Capability.GIT_AVAILABLE not in required


def test_dry_run_does_not_require_tools_used_only_by_skipped_mutations() -> None:
    steps = (
        _step("copy_files", StepCapability.MUTABLE_WITHOUT_SAFE_DRY_RUN),
        _step("copy_database", StepCapability.MUTABLE_WITHOUT_SAFE_DRY_RUN),
        _step("managed_plugin_refresh", StepCapability.MUTABLE_WITHOUT_SAFE_DRY_RUN),
    )

    required = required_capabilities(_config(managed_plugins=True), steps, dry_run=True)

    assert Capability.SSH_AVAILABLE not in required
    assert Capability.RSYNC_AVAILABLE not in required
    assert Capability.MYSQLDUMP_AVAILABLE not in required
    assert Capability.GIT_AVAILABLE not in required


def test_dry_run_source_inspection_requires_only_its_key_transport() -> None:
    required = required_capabilities(
        _config(),
        (_step("snapshot_source_database", StepCapability.READ_ONLY),),
        dry_run=True,
    )
    assert Capability.SSH_AVAILABLE in required
    assert Capability.RSYNC_AVAILABLE not in required
    assert Capability.MYSQLDUMP_AVAILABLE not in required


def test_password_transport_does_not_require_openssh_or_rsync() -> None:
    required = required_capabilities(
        _config(authentication="password"),
        (_step("copy_files", StepCapability.MUTABLE_WITHOUT_SAFE_DRY_RUN),),
        dry_run=False,
    )

    assert Capability.SSH_AVAILABLE not in required
    assert Capability.RSYNC_AVAILABLE not in required


def test_git_is_required_only_when_managed_plugin_refresh_will_run() -> None:
    step = _step("managed_plugin_refresh", StepCapability.MUTABLE_WITHOUT_SAFE_DRY_RUN)

    without_plugins = required_capabilities(_config(), (step,), dry_run=False)
    with_plugins = required_capabilities(_config(managed_plugins=True), (step,), dry_run=False)

    assert Capability.GIT_AVAILABLE not in without_plugins
    assert Capability.GIT_AVAILABLE in with_plugins


def test_probe_reports_exact_missing_executable_without_running_it() -> None:
    locator = FakeExecutableLocator(("php", "wp", "mysql"))
    runner = FakeCommandRunner([FakeCommandResult(), FakeCommandResult()])
    probe = CapabilityProbe(runner, FakeFileSystem(), executable_locator=locator)

    report = probe.probe(Path("/site"), {Capability.RSYNC_AVAILABLE})

    result = next(item for item in report.results if item.capability is Capability.RSYNC_AVAILABLE)
    assert not result.available
    assert result.detail == "executável obrigatório ausente: rsync"
    assert runner.calls == []


def test_missing_required_capability_blocks_before_run_creation_or_step() -> None:
    class MissingProbe:
        def probe(self, path, required_capabilities=None):
            del path, required_capabilities
            return CapabilityProbe(
                FakeCommandRunner(),
                FakeFileSystem(),
                executable_locator=FakeExecutableLocator(()),
            ).probe(Path("/site"), {Capability.RSYNC_AVAILABLE})

    state = FakeStateStore()
    operations = FakeOperations()
    manifest = RunManifest("run", "site", Operation.MIGRATE, RunStatus.RUNNING, "now", False)

    with pytest.raises(MissingCapabilityError, match="RSYNC_AVAILABLE"):
        PipelineRunner(MissingProbe(), state, FakeFileSystem(), FakeClock()).run(
            manifest,
            Path("/site"),
            [
                OperationStep(
                    _step("copy_files", StepCapability.MUTABLE_WITHOUT_SAFE_DRY_RUN), operations
                )
            ],
            {},
            {Capability.RSYNC_AVAILABLE},
        )

    assert state.manifests == {}
    assert operations.calls == []
