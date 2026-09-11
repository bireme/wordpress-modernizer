from __future__ import annotations

import re
import shlex
from dataclasses import replace
from pathlib import Path

from wp_modernizer.application.ports import CommandRunner, FileSystem
from wp_modernizer.domain.errors import ConfigurationError, WordPressUnavailableError
from wp_modernizer.domain.modernization import (
    FINAL_COMPATIBILITY,
    PhpRuntime,
    ProvisioningSuggestion,
    ResolvedTarget,
    RuntimeSelection,
    version,
)


def parse_wordpress_version(contents: str) -> str:
    matches = re.findall(
        r"^\s*\$wp_version\s*=\s*['\"](\d+\.\d+(?:\.\d+)?)['\"]\s*;", contents, re.M
    )
    if len(matches) != 1:
        raise WordPressUnavailableError("Cannot safely detect stable WordPress version")
    version(matches[0])
    return str(matches[0])


class LocalWordPressVersionInspector:
    def __init__(self, filesystem: FileSystem) -> None:
        self._filesystem = filesystem

    def inspect_version(self, path: Path) -> str:
        return parse_wordpress_version(
            self._filesystem.read_text(path / "wp-includes" / "version.php")
        )


class PhpRuntimeDiscovery:
    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    def inspect(self, runtime: PhpRuntime) -> PhpRuntime:
        try:
            result = self._runner.run([runtime.binary, "-v"], timeout=30)
        except Exception:
            return replace(runtime, installed=False, detected_version=None)
        match = re.search(r"^PHP (\d+\.\d+\.\d+)\s+\(cli\)", result.stdout, re.M)
        return replace(
            runtime,
            installed=result.return_code == 0 and match is not None,
            detected_version=match[1] if match else None,
        )


class ConfiguredTargetResolver:
    """Organization-controlled latest release, pinned before a run; no scraping."""

    def __init__(self, wordpress: str | None) -> None:
        self._wordpress = wordpress

    def resolve(self) -> ResolvedTarget:
        if self._wordpress is None:
            raise ConfigurationError(
                "Configure latest_wordpress with the latest approved stable release"
            )
        for supported, php in FINAL_COMPATIBILITY:
            if supported.contains(self._wordpress):
                return ResolvedTarget(self._wordpress, php)
        raise ConfigurationError("latest_wordpress is outside the audited compatibility policy")


class LinuxProvisioningAdvice:
    """Only return apt commands when the configured cache has a package candidate."""

    def __init__(self, runner: CommandRunner, filesystem: FileSystem) -> None:
        self._runner = runner
        self._filesystem = filesystem

    def suggest(self, selection: RuntimeSelection) -> ProvisioningSuggestion:
        runtime = selection.runtime
        requested = (
            runtime.configured_version
            if runtime
            else selection.requirement.exact or selection.requirement.minimum or "current"
        )
        minor = ".".join(requested.split(".")[:2])
        legacy = minor == "7.4"
        guidance = (
            "Automatic installation command is not available for this platform. "
            f"Install PHP {requested} CLI using your organization's approved package source."
        )
        if legacy:
            guidance += (
                " PHP 7.4 is EOL and a transient legacy runtime. Remove it after all old "
                "installations are modernized, if no other application depends on it."
            )
        binary = runtime.binary if runtime else f"/usr/bin/php{minor}"
        verify = (binary, "-v")
        fallback = ProvisioningSuggestion(
            requested, verify=verify, guidance=guidance, transient_legacy=legacy
        )
        if not re.fullmatch(r"\d+\.\d+", minor) or binary != f"/usr/bin/php{minor}":
            return fallback
        try:
            os_release = self._filesystem.read_text(Path("/etc/os-release"))
            values = dict(
                line.split("=", 1)
                for line in os_release.splitlines()
                if "=" in line and not line.startswith("#")
            )
            if shlex.split(values.get("ID", "")) not in (["debian"], ["ubuntu"]):
                return fallback
            package = f"php{minor}-cli"
            result = self._runner.run(
                ["apt-cache", "policy", package], timeout=30, environment={"LC_ALL": "C"}
            )
            if result.return_code != 0 or not re.search(
                r"^\s*Candidate:\s*(?!\(none\))\S+", result.stdout, re.M
            ):
                return fallback
        except Exception:
            return fallback
        return replace(
            fallback,
            install=("sudo", "apt", "install", package),
            remove=("sudo", "apt", "remove", package),
            guidance=(
                "Candidate found in configured APT cache; administrator must "
                "review package source and dependencies. "
                + (guidance[guidance.index("PHP 7.4 is EOL") :] if legacy else "")
            ),
        )
