"""Versioned, declarative modernization decisions; no host or network access."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


def version(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", value):
        raise ValueError(f"Invalid stable version: {value!r}")
    parts = tuple(int(part) for part in value.split("."))
    return (parts[0], parts[1], parts[2] if len(parts) == 3 else 0)


class ModernizationClass(str, Enum):
    ANCIENT = "ANCIENT"
    LEGACY = "LEGACY"
    CURRENT = "CURRENT"


@dataclass(frozen=True)
class WordPressVersionRange:
    minimum: str
    maximum: str

    def contains(self, value: str) -> bool:
        # Classification boundaries denote release families, including patch releases.
        return version(self.minimum)[:2] <= version(value)[:2] <= version(self.maximum)[:2]


@dataclass(frozen=True)
class PhpRuntimeRequirement:
    exact: str | None = None
    minimum: str | None = None
    maximum: str | None = None
    current: bool = False

    def __post_init__(self) -> None:
        for item in (self.exact, self.minimum, self.maximum):
            if item is not None:
                version(item)
        if self.exact and self.minimum:
            raise ValueError("PHP requirement cannot have both exact and minimum")

    def accepts(self, detected: str) -> bool:
        actual = version(detected)
        return (
            (self.exact is None or actual[:2] == version(self.exact)[:2])
            and (self.minimum is None or actual >= version(self.minimum))
            and (self.maximum is None or actual[:2] <= version(self.maximum)[:2])
        )


@dataclass(frozen=True)
class PhpRuntime:
    name: str
    binary: str
    configured_version: str
    installed: bool = False
    detected_version: str | None = None

    def __post_init__(self) -> None:
        if not Path(self.binary).is_absolute() or any(c in self.binary for c in "\x00\n\r"):
            raise ValueError("PHP binary must be an absolute path")
        version(self.configured_version)

    @property
    def verified(self) -> bool:
        return bool(
            self.installed
            and self.detected_version
            and version(self.detected_version)[:2] == version(self.configured_version)[:2]
        )


@dataclass(frozen=True)
class LegacyCheckpoint:
    wordpress: str
    php: PhpRuntimeRequirement


@dataclass(frozen=True)
class ResolvedTarget:
    wordpress: str
    php: PhpRuntimeRequirement


@dataclass(frozen=True)
class RuntimeSelection:
    requirement: PhpRuntimeRequirement
    runtime: PhpRuntime | None
    satisfies_requirement: bool


@dataclass(frozen=True)
class ModernizationStage:
    wordpress: str
    target: str
    php: RuntimeSelection


@dataclass(frozen=True)
class ModernizationRoute:
    policy_id: str
    policy_revision: int
    initial_wordpress: str
    modernization_class: ModernizationClass | None
    automation_supported: bool
    stages: tuple[ModernizationStage, ...] = ()
    runtimes: tuple[PhpRuntime, ...] = ()
    initial_php: RuntimeSelection | None = None
    reason_code: str | None = None
    manual_target_wordpress: str | None = None

    @property
    def ready(self) -> bool:
        return (
            self.automation_supported
            and bool(self.stages)
            and self.initial_php is not None
            and self.initial_php.satisfies_requirement
            and all(stage.php.satisfies_requirement for stage in self.stages)
        )


@dataclass(frozen=True)
class ModernizationPolicy:
    policy_id: str
    policy_revision: int
    legacy_range: WordPressVersionRange
    automatic_minimum: str
    manual_bridge_target: str
    checkpoints: tuple[LegacyCheckpoint, ...]
    final_php: PhpRuntimeRequirement

    def __post_init__(self) -> None:
        values = [version(checkpoint.wordpress) for checkpoint in self.checkpoints]
        if values != sorted(set(values)):
            raise ValueError("Checkpoints must be strictly increasing")
        version(self.automatic_minimum)
        version(self.manual_bridge_target)

    def classify(self, wordpress: str) -> ModernizationClass:
        if version(wordpress) < version(self.legacy_range.minimum):
            return ModernizationClass.ANCIENT
        if self.legacy_range.contains(wordpress):
            return ModernizationClass.LEGACY
        return ModernizationClass.CURRENT

    def route(
        self, wordpress: str, runtimes: tuple[PhpRuntime, ...], target: ResolvedTarget
    ) -> ModernizationRoute:
        classification = self.classify(wordpress)
        if version(wordpress) < version(self.automatic_minimum):
            return ModernizationRoute(
                self.policy_id,
                self.policy_revision,
                wordpress,
                classification,
                False,
                reason_code="ANCIENT_WORDPRESS_REQUIRES_MANUAL_BRIDGE",
                manual_target_wordpress=self.manual_bridge_target,
            )
        if version(target.wordpress) < max(
            version(wordpress), *(version(c.wordpress) for c in self.checkpoints)
        ):
            raise ValueError("Resolved latest would downgrade WordPress or omit policy checkpoints")
        stages = tuple(
            ModernizationStage(c.wordpress, c.wordpress, select_runtime(c.php, runtimes))
            for c in self.checkpoints
            if version(c.wordpress) > version(wordpress)
        )
        final_requirement = PhpRuntimeRequirement(
            minimum=max(
                (self.final_php.minimum or "0.0", target.php.minimum or "0.0"), key=version
            ),
            maximum=target.php.maximum or self.final_php.maximum,
            current=True,
        )
        initial_requirement = final_requirement
        next_checkpoint = next(
            (
                checkpoint
                for checkpoint in self.checkpoints
                if version(checkpoint.wordpress) > version(wordpress)
            ),
            None,
        )
        if next_checkpoint is not None:
            initial_requirement = next_checkpoint.php
        elif classification is ModernizationClass.LEGACY:
            initial_requirement = self.checkpoints[-1].php
        return ModernizationRoute(
            self.policy_id,
            self.policy_revision,
            wordpress,
            classification,
            True,
            (
                *stages,
                ModernizationStage(
                    target.wordpress, "latest", select_runtime(final_requirement, runtimes)
                ),
            ),
            runtimes,
            select_runtime(initial_requirement, runtimes),
        )


def select_runtime(
    requirement: PhpRuntimeRequirement, runtimes: tuple[PhpRuntime, ...]
) -> RuntimeSelection:
    candidates = [
        r
        for r in runtimes
        if (not requirement.current or r.name == "current")
        and requirement.accepts(r.configured_version)
    ]
    candidates.sort(key=lambda r: (r.verified, version(r.configured_version), r.name), reverse=True)
    selected = candidates[0] if candidates else None
    return RuntimeSelection(
        requirement,
        selected,
        bool(
            selected
            and selected.verified
            and selected.detected_version
            and requirement.accepts(selected.detected_version)
        ),
    )


DEFAULT_POLICY = ModernizationPolicy(
    policy_id="wordpress-official-legacy-2026-09",
    policy_revision=1,
    legacy_range=WordPressVersionRange("4.9", "6.8"),
    automatic_minimum="4.9",
    manual_bridge_target="4.9",
    checkpoints=(
        LegacyCheckpoint("5.3", PhpRuntimeRequirement(exact="7.4")),
        LegacyCheckpoint("6.2", PhpRuntimeRequirement(exact="7.4")),
        LegacyCheckpoint("6.8", PhpRuntimeRequirement(minimum="8.1", maximum="8.4")),
    ),
    final_php=PhpRuntimeRequirement(minimum="8.1", current=True),
)

# Audited compatibility families. Unknown future releases fail closed until reviewed.
FINAL_COMPATIBILITY = (
    (WordPressVersionRange("6.9", "6.9"), PhpRuntimeRequirement(minimum="7.2.24", maximum="8.5")),
    (WordPressVersionRange("7.0", "7.1"), PhpRuntimeRequirement(minimum="7.4", maximum="8.5")),
)


@dataclass(frozen=True)
class ProvisioningSuggestion:
    runtime: str
    install: tuple[str, ...] = ()
    verify: tuple[str, ...] = ()
    remove: tuple[str, ...] = ()
    guidance: str = ""
    transient_legacy: bool = False
