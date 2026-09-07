from __future__ import annotations

from wp_modernizer.application.ports import (
    LocalWordPressVersionPort,
    PhpRuntimeDiscoveryPort,
    ProvisioningAdvicePort,
    TargetVersionPort,
    WordPressVersionPort,
)
from wp_modernizer.config.models import ApplicationConfig
from wp_modernizer.domain.errors import ConfigurationError, MissingCapabilityError, ModernizerError
from wp_modernizer.domain.models import PlannedStep
from wp_modernizer.domain.modernization import (
    DEFAULT_POLICY,
    ModernizationClass,
    ModernizationPolicy,
    ModernizationRoute,
    PhpRuntime,
    PhpRuntimeRequirement,
    ResolvedTarget,
    RuntimeSelection,
)


class ModernizationPlanning:
    def __init__(
        self,
        config: ApplicationConfig,
        source: WordPressVersionPort,
        local_source: LocalWordPressVersionPort,
        discovery: PhpRuntimeDiscoveryPort,
        target: TargetVersionPort,
        advice: ProvisioningAdvicePort,
        policy: ModernizationPolicy = DEFAULT_POLICY,
    ) -> None:
        self.config = config
        self.source = source
        self.local_source = local_source
        self.discovery = discovery
        self.target = target
        self.advice = advice
        self.policy = policy

    def route(self, installation_id: str, *, local: bool = False) -> ModernizationRoute:
        try:
            return self._route(installation_id, local=local)
        except (ModernizerError, OSError):
            return ModernizationRoute(
                self.policy.policy_id,
                self.policy.policy_revision,
                "unknown",
                None,
                False,
                reason_code="WORDPRESS_VERSION_UNAVAILABLE",
            )

    def _route(self, installation_id: str, *, local: bool) -> ModernizationRoute:
        item = self.config.installations[installation_id]
        if local:
            # Parsing is deliberately a non-executing port even for local TEST.
            detected = self.local_source.inspect_version(item.effective_destination_path)
        else:
            detected = self.source.inspect_version(item.source_server, item.source_path, "plan")
        if self.policy.classify(detected) is ModernizationClass.ANCIENT:
            return self.policy.route(
                detected, (), ResolvedTarget(detected, PhpRuntimeRequirement())
            )
        try:
            target = self.target.resolve()
            if item.core_checkpoints:
                raise ConfigurationError("core_checkpoints is obsolete; use the versioned policy")
            runtimes = tuple(
                self.discovery.inspect(PhpRuntime(name, str(cfg.binary), cfg.version))
                for name, cfg in sorted(self.config.php_runtimes.items())
            )
            return self.policy.route(detected, runtimes, target)
        except (ConfigurationError, ValueError):
            return ModernizationRoute(
                self.policy.policy_id,
                self.policy.policy_revision,
                detected,
                self.policy.classify(detected),
                False,
                reason_code="TARGET_POLICY_CONFIGURATION_REQUIRED",
            )

    def assert_ready(self, routes: dict[str, ModernizationRoute]) -> None:
        for installation_id, route in routes.items():
            if not route.ready:
                if route.manual_target_wordpress:
                    raise MissingCapabilityError(
                        f"{installation_id}: {route.reason_code}. "
                        "Automatic modernization starts at "
                        f"WordPress {route.manual_target_wordpress}. "
                        "Use a compatible legacy environment "
                        "for the manual bridge, then run inventory/diagnose/plan again."
                    )
                missing = [
                    self.describe(stage.php)
                    for stage in route.stages
                    if not stage.php.satisfies_requirement
                ]
                raise MissingCapabilityError(
                    f"{installation_id}: BLOCKED: {', '.join(missing) or route.reason_code}"
                )

    def check_remaining(self, steps: list[PlannedStep]) -> None:
        for step in steps:
            selection = step.php
            if selection is None:
                raise MissingCapabilityError("Old run lacks explicit PHP routing; start a new plan")
            runtime = selection.runtime
            if runtime is None:
                raise MissingCapabilityError(f"{step.name}: PHP runtime was not selected")
            inspected = self.discovery.inspect(runtime)
            if (
                not inspected.verified
                or not inspected.detected_version
                or not selection.requirement.accepts(inspected.detected_version)
            ):
                raise MissingCapabilityError(
                    f"{step.name}: PHP missing or incompatible: {runtime.binary}"
                )

    @staticmethod
    def describe(selection: RuntimeSelection) -> str:
        runtime = selection.runtime
        return (
            f"PHP {runtime.configured_version} ({runtime.binary})"
            if runtime
            else (
                f"PHP {selection.requirement.exact or selection.requirement.minimum or 'current'}"
                ": configure runtime"
            )
        )
