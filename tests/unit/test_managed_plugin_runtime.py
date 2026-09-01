from pathlib import Path
from types import SimpleNamespace

from tests.fakes.core import FakeCommandResult, FakeCommandRunner
from wp_modernizer.domain.enums import Environment, ManagedPluginStatus, Operation, RunStatus
from wp_modernizer.domain.models import ManagedPlugin, PlannedStep, RunManifest
from wp_modernizer.domain.path_parser import InstallationPathParser
from wp_modernizer.infrastructure.filesystem import LocalFileSystem
from wp_modernizer.infrastructure.managed_plugins import ManagedPluginRefresher
from wp_modernizer.infrastructure.runtime_operations import RuntimeOperations


def test_runtime_records_abort_result_in_manifest(tmp_path: Path) -> None:
    site = tmp_path / "example.org" / "wp-test" / "htdocs"
    target = site / "wp-content" / "plugins" / "managed"
    target.mkdir(parents=True)
    runner = FakeCommandRunner([FakeCommandResult(stdout=" M local.php\n")])
    managed = ManagedPlugin(
        "managed",
        "https://example.invalid/plugin.git",
        "stable",
        "replace_from_git",
        "abort",
    )
    manifest = RunManifest(
        "run-1",
        "site",
        Operation.UPDATE,
        RunStatus.RUNNING,
        "now",
        False,
        managed_plugins=[managed],
    )
    operations = RuntimeOperations(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        InstallationPathParser([tmp_path]),
        managed_plugins=ManagedPluginRefresher(LocalFileSystem(), runner),
    )

    result = operations.execute(
        "managed_plugin_refresh",
        {
            "run_id": "run-1",
            "installation": SimpleNamespace(
                destination_environment=Environment.TEST,
                destination_path=site,
            ),
            "installations": {},
            "manifest": manifest,
            "planned_step": PlannedStep("managed_plugin_refresh", True, True, "", "", "site"),
        },
    )

    assert result.status.value == "FAILED"
    assert manifest.managed_plugin_results[0].status is ManagedPluginStatus.FAILED_PRESERVED


def test_third_party_update_excludes_managed_plugin_slugs(tmp_path: Path) -> None:
    class WordPress:
        arguments = ()

        def update(self, path, arguments, run_id):
            del path, run_id
            self.arguments = tuple(arguments)
            return "updated"

    wordpress = WordPress()
    managed = ManagedPlugin(
        "managed",
        "https://example.invalid/plugin.git",
        "stable",
        "replace_from_git",
        "abort",
    )
    manifest = RunManifest(
        "run-1",
        "site",
        Operation.UPDATE,
        RunStatus.RUNNING,
        "now",
        False,
        managed_plugins=[managed],
    )
    site = tmp_path / "example.org" / "wp-test" / "htdocs"
    operations = RuntimeOperations(
        SimpleNamespace(),
        SimpleNamespace(),
        wordpress,
        InstallationPathParser([tmp_path]),
    )

    operations.execute(
        "third_party_plugin_update",
        {
            "run_id": "run-1",
            "installation": SimpleNamespace(
                destination_environment=Environment.TEST,
                destination_path=site,
            ),
            "installations": {},
            "manifest": manifest,
            "planned_step": PlannedStep("third_party_plugin_update", True, True, "", "", "site"),
        },
    )

    assert wordpress.arguments == ("plugin", "update", "--all", "--exclude=managed")
