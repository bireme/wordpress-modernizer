from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from tests.fakes.core import (
    FakeClock,
    FakeCommandResult,
    FakeCommandRunner,
    FakeFileSystem,
    FakeIds,
    FakeOperations,
    FakeProbe,
    FakeStateStore,
    health,
)
from wp_modernizer.application.modernization import ModernizationPlanning
from wp_modernizer.application.service import ModernizerService
from wp_modernizer.config.models import ApplicationConfig, PhpRuntimeConfig
from wp_modernizer.domain.enums import Environment, HealthStatus, Operation
from wp_modernizer.domain.errors import MissingCapabilityError
from wp_modernizer.domain.models import PlannedStep
from wp_modernizer.domain.modernization import (
    DEFAULT_POLICY,
    LegacyCheckpoint,
    ModernizationClass,
    ModernizationPolicy,
    PhpRuntime,
    PhpRuntimeRequirement,
    ProvisioningSuggestion,
    ResolvedTarget,
    WordPressVersionRange,
)
from wp_modernizer.infrastructure.modernization import (
    LinuxProvisioningAdvice,
    PhpRuntimeDiscovery,
    parse_wordpress_version,
)
from wp_modernizer.infrastructure.runtime_operations import RuntimeOperations
from wp_modernizer.infrastructure.state import JsonStateStore
from wp_modernizer.infrastructure.wpcli.adapter import WPCLIAdapter


def installed(name: str, binary: str, runtime_version: str) -> PhpRuntime:
    return PhpRuntime(name, binary, runtime_version, True, f"{runtime_version}.12")


RUNTIMES = (
    installed("7.4", "/usr/bin/php7.4", "7.4"),
    installed("8.2", "/usr/bin/php8.2", "8.2"),
    installed("current", "/usr/bin/php8.5", "8.5"),
)
TARGET = ResolvedTarget("7.1", PhpRuntimeRequirement(minimum="7.4", maximum="8.5"))


@pytest.mark.parametrize(
    ("wordpress", "classification"),
    [
        ("4.8.99", ModernizationClass.ANCIENT),
        ("4.9", ModernizationClass.LEGACY),
        ("5.2", ModernizationClass.LEGACY),
        ("5.3", ModernizationClass.LEGACY),
        ("6.2", ModernizationClass.LEGACY),
        ("6.8", ModernizationClass.LEGACY),
        ("6.9", ModernizationClass.CURRENT),
    ],
)
def test_classification(wordpress: str, classification: ModernizationClass) -> None:
    assert DEFAULT_POLICY.classify(wordpress) is classification


@pytest.mark.parametrize(
    ("wordpress", "route"),
    [
        ("4.9", ["5.3", "6.2", "6.8", "latest"]),
        ("5.3", ["6.2", "6.8", "latest"]),
        ("5.8", ["6.2", "6.8", "latest"]),
        ("6.2", ["6.8", "latest"]),
        ("6.5", ["6.8", "latest"]),
        ("6.8", ["latest"]),
        ("6.9", ["latest"]),
    ],
)
def test_route_contains_only_strictly_later_checkpoints(wordpress: str, route: list[str]) -> None:
    planned = DEFAULT_POLICY.route(wordpress, RUNTIMES, TARGET)
    assert [stage.target for stage in planned.stages] == route


def test_wordpress_6_8_preflight_uses_compatible_php_before_current() -> None:
    route = DEFAULT_POLICY.route("6.8", RUNTIMES, TARGET)
    assert [stage.target for stage in route.stages] == ["latest"]
    assert route.initial_php and route.initial_php.runtime
    assert route.initial_php.runtime.binary == "/usr/bin/php8.2"
    assert route.stages[-1].php.runtime
    assert route.stages[-1].php.runtime.binary == "/usr/bin/php8.5"


def test_checkpoint_and_final_php_routing() -> None:
    stages = DEFAULT_POLICY.route("4.9", RUNTIMES, TARGET).stages
    assert stages[0].php.requirement.exact == "7.4"
    assert stages[0].php.runtime and stages[0].php.runtime.binary == "/usr/bin/php7.4"
    assert stages[1].php.requirement.exact == "7.4"
    assert stages[2].php.requirement.minimum == "8.1"
    assert stages[2].php.runtime and stages[2].php.runtime.binary == "/usr/bin/php8.2"
    assert stages[3].php.requirement.current
    assert stages[3].php.runtime and stages[3].php.runtime.binary == "/usr/bin/php8.5"


@pytest.mark.parametrize("runtime_version", ["8.1", "8.2", "8.3", "8.4"])
def test_wordpress_6_8_requirement_accepts_audited_php_versions(
    runtime_version: str,
) -> None:
    requirement = DEFAULT_POLICY.checkpoints[-1].php
    assert requirement.accepts(f"{runtime_version}.1")


def test_missing_and_wrong_runtime_are_not_ready() -> None:
    missing = PhpRuntime("7.4", "/usr/bin/php7.4", "7.4")
    wrong = PhpRuntime("current", "/usr/bin/php8.5", "8.5", True, "8.4.9")
    route = DEFAULT_POLICY.route("4.9", (missing, wrong), TARGET)
    assert not route.ready
    assert not route.stages[0].php.satisfies_requirement
    assert not route.stages[-1].php.satisfies_requirement


def test_php_runtime_configuration_requires_absolute_safe_path() -> None:
    with pytest.raises(ValidationError, match="absolute"):
        PhpRuntimeConfig(binary=Path("php7.4"), version="7.4")
    with pytest.raises(ValidationError, match="version"):
        PhpRuntimeConfig(binary=Path("/usr/bin/php"), version="shell command")


def test_ancient_is_structurally_blocked() -> None:
    route = DEFAULT_POLICY.route("4.8.99", (), TARGET)
    assert route.modernization_class is ModernizationClass.ANCIENT
    assert not route.automation_supported
    assert route.manual_target_wordpress == "4.9"
    assert route.reason_code == "ANCIENT_WORDPRESS_REQUIRES_MANUAL_BRIDGE"


def test_future_policy_can_automate_ancient_by_extending_policy_data() -> None:
    policy = ModernizationPolicy(
        policy_id="future-ancient-policy",
        policy_revision=1,
        legacy_range=WordPressVersionRange("4.9", "6.8"),
        automatic_minimum="4.1",
        manual_bridge_target="4.1",
        checkpoints=(
            LegacyCheckpoint("4.9", PhpRuntimeRequirement(exact="7.2")),
            *DEFAULT_POLICY.checkpoints,
        ),
        final_php=DEFAULT_POLICY.final_php,
    )
    runtimes = (
        installed("7.2", "/usr/bin/php7.2", "7.2"),
        *RUNTIMES,
    )

    route = policy.route("4.8", runtimes, TARGET)

    assert route.modernization_class is ModernizationClass.ANCIENT
    assert route.automation_supported
    assert route.initial_php and route.initial_php.runtime
    assert route.initial_php.runtime.binary == "/usr/bin/php7.2"
    assert [stage.target for stage in route.stages] == [
        "4.9",
        "5.3",
        "6.2",
        "6.8",
        "latest",
    ]


@given(major=st.integers(min_value=0, max_value=4), minor=st.integers(0, 8))
def test_no_release_family_before_4_9_is_legacy(major: int, minor: int) -> None:
    assert DEFAULT_POLICY.classify(f"{major}.{minor}.99") is ModernizationClass.ANCIENT


@given(
    major_minor=st.sampled_from(
        [(4, 9), *[(5, minor) for minor in range(10)], *[(6, minor) for minor in range(9)]]
    ),
    patch=st.integers(0, 999),
)
def test_every_release_in_legacy_range_is_legacy(major_minor: tuple[int, int], patch: int) -> None:
    major, minor = major_minor
    assert DEFAULT_POLICY.classify(f"{major}.{minor}.{patch}") is ModernizationClass.LEGACY


def test_runtime_discovery_executes_configured_absolute_binary() -> None:
    runner = FakeCommandRunner([FakeCommandResult(stdout="PHP 7.4.33 (cli)\n")])
    result = PhpRuntimeDiscovery(runner).inspect(PhpRuntime("7.4", "/usr/bin/php7.4", "7.4"))
    assert result.verified
    assert result.detected_version == "7.4.33"
    assert runner.calls == [("/usr/bin/php7.4", "-v")]


def test_wpcli_uses_explicit_php_without_changing_global_runtime() -> None:
    runner = FakeCommandRunner([FakeCommandResult(stdout="6.8")])
    adapter = WPCLIAdapter(runner, "/usr/local/bin/wp").with_runtime("/usr/bin/php7.4")
    adapter.update(Path("/srv/test"), ("core", "version"), "run")
    assert runner.calls[0][:2] == ("/usr/bin/php7.4", "/usr/local/bin/wp")
    assert all("update-alternatives" not in argument for argument in runner.calls[0])


def test_wordpress_version_parser_does_not_execute_php() -> None:
    assert parse_wordpress_version("<?php\n$wp_version = '4.9.26';\n") == "4.9.26"


def test_apt_advice_is_informational_and_requires_candidate() -> None:
    class UbuntuFileSystem(FakeFileSystem):
        def read_text(self, path: Path) -> str:
            assert path == Path("/etc/os-release")
            return "ID=ubuntu\n"

    filesystem = UbuntuFileSystem()
    runner = FakeCommandRunner([FakeCommandResult(stdout="php7.4-cli:\n  Candidate: 7.4.33-1\n")])
    runtime = PhpRuntime("7.4", "/usr/bin/php7.4", "7.4")
    selection = DEFAULT_POLICY.route("4.9", (runtime,), TARGET).stages[0].php
    advice = LinuxProvisioningAdvice(runner, filesystem).suggest(selection)
    assert advice.install == ("sudo", "apt", "install", "php7.4-cli")
    assert advice.verify == ("/usr/bin/php7.4", "-v")
    assert advice.remove == ("sudo", "apt", "remove", "php7.4-cli")
    assert advice.transient_legacy
    assert runner.calls == [("apt-cache", "policy", "php7.4-cli")]


def configured_service(wordpress_version: str) -> tuple[ModernizerService, FakeOperations]:
    config = ApplicationConfig.model_validate(
        {
            "state_directory": "state",
            "latest_wordpress": "7.1",
            "wpcli_binary": "/usr/local/bin/wp",
            "php_runtimes": {
                "7.4": {"binary": "/usr/bin/php7.4", "version": "7.4"},
                "8.2": {"binary": "/usr/bin/php8.2", "version": "8.2"},
                "current": {"binary": "/usr/bin/php8.5", "version": "8.5"},
            },
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
                "site": {
                    "source_server": "source",
                    "source_environment": "production",
                    "source_path": "/home/apps/example.org/wp-main/htdocs",
                    "destination_path": "/home/apps/example.org/wp-test/htdocs",
                    "allowed_database_endpoints": ["db"],
                }
            },
        }
    )

    class Source:
        def inspect_version(self, server_id: str, path: Path, run_id: str) -> str:
            del server_id, path, run_id
            return wordpress_version

    class Local:
        def inspect_version(self, path: Path) -> str:
            del path
            return wordpress_version

    class Discovery:
        def inspect(self, runtime: PhpRuntime) -> PhpRuntime:
            if runtime.name == "7.4":
                return runtime
            return replace(
                runtime,
                installed=True,
                detected_version=f"{runtime.configured_version}.1",
            )

    class Target:
        def resolve(self) -> ResolvedTarget:
            return TARGET

    class Advice:
        calls = 0

        def suggest(self, selection: object) -> ProvisioningSuggestion:
            del selection
            self.calls += 1
            return ProvisioningSuggestion("7.4", guidance="manual only")

    operations = FakeOperations()
    planning = ModernizationPlanning(
        config,
        Source(),
        Local(),
        Discovery(),
        Target(),
        Advice(),  # type: ignore[arg-type]
    )
    return (
        ModernizerService(
            config,
            FakeProbe([health(HealthStatus.HEALTHY)]),
            FakeStateStore(),
            FakeFileSystem(),
            FakeClock(),
            FakeIds(),
            operations,
            modernization=planning,
        ),
        operations,
    )


def test_plan_has_typed_route_runtime_inventory_missing_and_readiness() -> None:
    service, _ = configured_service("4.9.26")

    payload = service.plan("site")
    route = payload["modernization"]["site"]

    assert route["modernization_class"] == "LEGACY"
    assert route["initial_wordpress"] == "4.9.26"
    assert [stage["target"] for stage in route["stages"]] == [
        "5.3",
        "6.2",
        "6.8",
        "latest",
    ]
    assert {runtime["name"] for runtime in route["runtimes"]} == {"7.4", "8.2", "current"}
    assert payload["missing_php_runtimes"][0]["requirement"]["exact"] == "7.4"
    assert payload["execution_readiness"]["status"] == "BLOCKED"
    assert payload["provisioning_suggestions"][0]["guidance"] == "manual only"


def test_ancient_pipeline_blocks_before_any_wordpress_operation() -> None:
    service, operations = configured_service("4.8.99")

    with pytest.raises(MissingCapabilityError, match="ANCIENT_WORDPRESS_REQUIRES_MANUAL_BRIDGE"):
        service.execute(Operation.PIPELINE, "site", dry_run=False)

    assert operations.calls == []


def test_route_round_trip_preserves_policy_and_runtime_selection() -> None:
    original = DEFAULT_POLICY.route("4.9", RUNTIMES, TARGET)
    serialized = JsonStateStore._serialize(original)
    restored = JsonStateStore._deserialize_route(serialized)
    assert restored == original
    assert restored.policy_id == "wordpress-official-legacy-2026-09"
    assert restored.policy_revision == 1


def test_resume_preflight_rejects_a_runtime_that_disappeared() -> None:
    class MissingDiscovery:
        def inspect(self, runtime: PhpRuntime) -> PhpRuntime:
            return PhpRuntime(runtime.name, runtime.binary, runtime.configured_version)

    planning = object.__new__(ModernizationPlanning)
    planning.discovery = MissingDiscovery()  # type: ignore[assignment]
    selection = DEFAULT_POLICY.route("4.9", RUNTIMES, TARGET).stages[0].php
    remaining = [
        PlannedStep(
            "wordpress_checkpoint_5.3",
            True,
            True,
            "validated",
            "preserved",
            "site",
            php=selection,
            wordpress_target="5.3",
        )
    ]

    with pytest.raises(MissingCapabilityError, match=r"/usr/bin/php7\.4"):
        planning.check_remaining(remaining)


def test_checkpoint_executes_core_database_and_validation_under_selected_php() -> None:
    class WordPress:
        def __init__(self) -> None:
            self.php: str | None = None
            self.calls: list[tuple[str, ...]] = []
            self.versions = iter(("4.9.26", "5.3"))

        def with_runtime(self, binary: str) -> "WordPress":
            self.php = binary
            return self

        def update(self, path: Path, arguments: tuple[str, ...], run_id: str) -> str:
            del path, run_id
            self.calls.append(arguments)
            if arguments == ("core", "version"):
                return next(self.versions)
            return "ok"

        def get_site_url(self, path: Path, run_id: str) -> str:
            del path, run_id
            return "https://test.example.org"

    class Discovery:
        def inspect(self, runtime: PhpRuntime) -> PhpRuntime:
            return runtime

    wordpress = WordPress()
    selection = DEFAULT_POLICY.route("4.9", RUNTIMES, TARGET).stages[0].php
    step = PlannedStep(
        "wordpress_checkpoint_5.3",
        True,
        True,
        "validated checkpoint",
        "preserve TEST",
        "site",
        php=selection,
        wordpress_target="5.3",
    )
    operations = RuntimeOperations(
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        wordpress,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        runtime_discovery=Discovery(),
    )
    installation = type(
        "Installation",
        (),
        {
            "destination_environment": Environment.TEST,
            "destination_path": Path("/srv/test"),
            "source_path": Path("/srv/source"),
        },
    )()

    result = operations.execute(
        step.name,
        {
            "planned_step": step,
            "installation": installation,
            "installations": {},
            "run_id": "run",
        },
    )

    assert result.status.value == "EXECUTED"
    assert wordpress.php == "/usr/bin/php7.4"
    assert wordpress.calls == [
        ("core", "version"),
        ("core", "update", "--version=5.3"),
        ("core", "update-db"),
        ("core", "version"),
        ("core", "verify-checksums"),
    ]
