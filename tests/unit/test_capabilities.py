from pathlib import Path

from tests.fakes.core import FakeCommandResult, FakeCommandRunner, FakeFileSystem
from wp_modernizer.diagnostics.capability import CapabilityProbe
from wp_modernizer.domain.enums import Capability, HealthStatus

PATH = Path("/site")


def run_probe(codes, config="<?php define('MULTISITE', true);"):
    files = {PATH / "wp-config.php": config, PATH / "wp-includes" / "version.php": "version"}
    runner = FakeCommandRunner(
        [FakeCommandResult(code, "ok" if code == 0 else "Fatal error") for code in codes]
    )
    return CapabilityProbe(runner, FakeFileSystem(files)).probe(PATH)


def test_healthy() -> None:
    report = run_probe([0, 0, 0, 0, 0, 0])
    assert report.health is HealthStatus.HEALTHY
    assert report.has(Capability.MULTISITE)


def test_plugin_or_theme_fatal() -> None:
    report = run_probe([0, 0, 0, 0, 0, 1])
    assert report.health is HealthStatus.PLUGIN_OR_THEME_CONFLICT
    assert report.fatal_errors


def test_wpcli_partial() -> None:
    assert run_probe([0, 0, 0, 0, 1, 1]).health is HealthStatus.WPCLI_PARTIAL


def test_wpcli_absent() -> None:
    assert run_probe([0, 0, 1]).health is HealthStatus.PRE_BOOTSTRAP_RECOVERY_REQUIRED


def test_invalid_config() -> None:
    assert run_probe([0, 1, 0, 0, 0, 0]).health is HealthStatus.PHP_CONFIG_ERROR


def test_core_incomplete() -> None:
    files = {PATH / "wp-config.php": "<?php"}
    report = CapabilityProbe(
        FakeCommandRunner([FakeCommandResult() for _ in range(6)]), FakeFileSystem(files)
    ).probe(PATH)
    assert report.health is HealthStatus.CORE_INCOMPLETE
