from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

from wp_modernizer.application.ports import (
    CommandRunner,
    DatabaseProbePort,
    FileSystem,
    WordPressPort,
)
from wp_modernizer.domain.enums import Capability, DatabaseAvailabilityStatus, HealthStatus
from wp_modernizer.domain.models import CapabilityReport, DatabaseProbeResult, ProbeResult


class CapabilityProbe:
    def __init__(
        self,
        runner: CommandRunner,
        filesystem: FileSystem,
        wp_bin: str = "wp",
        php_bin: str = "php",
        database: DatabaseProbePort | None = None,
        wordpress: WordPressPort | None = None,
        database_endpoints: Mapping[Path, Sequence[str]] | None = None,
    ) -> None:
        self._runner = runner
        self._filesystem = filesystem
        self._wp = wp_bin
        self._php = php_bin
        self._database = database
        self._wordpress = wordpress
        self._database_endpoints = database_endpoints or {}

    def probe(self, installation_path: Path) -> CapabilityReport:
        config = installation_path / "wp-config.php"
        version_file = installation_path / "wp-includes" / "version.php"
        results: Dict[Capability, ProbeResult] = {}
        php = self._command([self._php, "--version"], Capability.PHP_AVAILABLE)
        results[Capability.PHP_AVAILABLE] = php
        config_valid = self._filesystem.exists(config)
        if config_valid and php.available:
            lint = self._command([self._php, "-l", str(config)], Capability.WP_CONFIG_VALID)
        else:
            lint = ProbeResult(Capability.WP_CONFIG_VALID, False, "wp-config.php indisponível")
        results[Capability.WP_CONFIG_VALID] = lint
        results[Capability.WP_CORE_DETECTED] = ProbeResult(
            Capability.WP_CORE_DETECTED,
            self._filesystem.exists(version_file),
            "arquivo de versão do núcleo presente"
            if self._filesystem.exists(version_file)
            else "version.php ausente",
        )
        results[Capability.MULTISITE] = ProbeResult(
            Capability.MULTISITE, self._detect_multisite(config) if config_valid else False
        )
        cli = self._command([self._wp, "--info"], Capability.WPCLI_AVAILABLE)
        results[Capability.WPCLI_AVAILABLE] = cli
        pre = (
            self._command(
                [self._wp, f"--path={installation_path}", "core", "version"],
                Capability.WPCLI_PRE_BOOTSTRAP,
            )
            if cli.available
            else ProbeResult(Capability.WPCLI_PRE_BOOTSTRAP, False, "WP-CLI ausente")
        )
        results[Capability.WPCLI_PRE_BOOTSTRAP] = pre
        reduced = (
            self._command(
                [
                    self._wp,
                    f"--path={installation_path}",
                    "--skip-plugins",
                    "--skip-themes",
                    "option",
                    "get",
                    "siteurl",
                ],
                Capability.WPCLI_REDUCED_BOOTSTRAP,
            )
            if cli.available
            else ProbeResult(Capability.WPCLI_REDUCED_BOOTSTRAP, False, "WP-CLI ausente")
        )
        results[Capability.WPCLI_REDUCED_BOOTSTRAP] = reduced
        full = (
            self._command(
                [self._wp, f"--path={installation_path}", "option", "get", "siteurl"],
                Capability.WPCLI_FULL_BOOTSTRAP,
            )
            if cli.available
            else ProbeResult(Capability.WPCLI_FULL_BOOTSTRAP, False, "WP-CLI ausente")
        )
        results[Capability.WPCLI_FULL_BOOTSTRAP] = full
        results[Capability.DATABASE_AVAILABLE] = self._probe_database(
            installation_path, lint.available, cli.available
        )
        ordered = tuple(results[item] for item in Capability)
        return CapabilityReport(ordered, self._classify(results), self._fatal_errors(full, reduced))

    def _command(self, argv: List[str], capability: Capability) -> ProbeResult:
        try:
            result = self._runner.run(argv, timeout=90)
            message = (result.stdout or result.stderr)[-4000:]
            return ProbeResult(capability, result.return_code == 0, message)
        except Exception as exc:
            return ProbeResult(capability, False, str(exc))

    def _detect_multisite(self, config: Path) -> bool:
        text = self._filesystem.read_text(config)
        compact = "".join(line.split("//", 1)[0] for line in text.splitlines())
        return "MULTISITE" in compact and "true" in compact.lower()

    def _probe_database(
        self, installation_path: Path, config_valid: bool, wpcli_available: bool
    ) -> ProbeResult:
        insufficient = ProbeResult(
            Capability.DATABASE_AVAILABLE, False, "configuração insuficiente"
        )
        endpoints = tuple(self._database_endpoints.get(installation_path, ()))
        if (
            not config_valid
            or not wpcli_available
            or self._database is None
            or self._wordpress is None
            or not endpoints
        ):
            return insufficient
        try:
            database_name = self._wordpress.get_config(
                installation_path, "DB_NAME", "capability-probe"
            ).strip()
        except Exception:
            return insufficient
        if not database_name:
            return insufficient

        evidence = [
            self._database.probe_database(endpoint_id, database_name) for endpoint_id in endpoints
        ]
        available = next((item for item in evidence if item.available), None)
        if available is not None:
            return ProbeResult(Capability.DATABASE_AVAILABLE, True, available.detail)

        precedence = (
            DatabaseAvailabilityStatus.SCHEMA_NOT_FOUND,
            DatabaseAvailabilityStatus.AUTHENTICATION_DENIED,
            DatabaseAvailabilityStatus.ENDPOINT_UNAVAILABLE,
            DatabaseAvailabilityStatus.CONFIGURATION_INSUFFICIENT,
            DatabaseAvailabilityStatus.UNKNOWN,
        )
        selected = next(
            (item for status in precedence for item in evidence if item.status is status),
            DatabaseProbeResult(DatabaseAvailabilityStatus.UNKNOWN, "estado do banco desconhecido"),
        )
        return ProbeResult(Capability.DATABASE_AVAILABLE, False, selected.detail)

    @staticmethod
    def _classify(results: Dict[Capability, ProbeResult]) -> HealthStatus:
        def has(item: Capability) -> bool:
            return results[item].available

        if not has(Capability.PHP_AVAILABLE) or not has(Capability.WP_CONFIG_VALID):
            return HealthStatus.PHP_CONFIG_ERROR
        if not has(Capability.DATABASE_AVAILABLE):
            return HealthStatus.DATABASE_UNAVAILABLE
        if not has(Capability.WP_CORE_DETECTED):
            return HealthStatus.CORE_INCOMPLETE
        if has(Capability.WPCLI_FULL_BOOTSTRAP):
            return HealthStatus.HEALTHY
        if has(Capability.WPCLI_REDUCED_BOOTSTRAP):
            return HealthStatus.PLUGIN_OR_THEME_CONFLICT
        if has(Capability.WPCLI_PRE_BOOTSTRAP) or has(Capability.WPCLI_AVAILABLE):
            return HealthStatus.WPCLI_PARTIAL
        return HealthStatus.PRE_BOOTSTRAP_RECOVERY_REQUIRED

    @staticmethod
    def _fatal_errors(full: ProbeResult, reduced: ProbeResult) -> Tuple[str, ...]:
        text = "\n".join((full.detail, reduced.detail))
        return tuple(line for line in text.splitlines() if "fatal" in line.lower())[-50:]
