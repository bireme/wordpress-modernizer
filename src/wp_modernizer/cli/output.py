from __future__ import annotations

from typing import Iterable, cast

import click

from wp_modernizer.domain.enums import Capability, HealthStatus, RunStatus, StepStatus
from wp_modernizer.domain.models import CapabilityReport, RunManifest, StepResult
from wp_modernizer.pipeline.progress import ProgressReporter
from wp_modernizer.security.redaction import Redactor

STEP_LABELS = {
    "preflight": "Checking destination",
    "backup_existing_test": "Backing up existing test site",
    "copy_files": "Copying WordPress files",
    "snapshot_source_database": "Inspecting source database",
    "copy_database": "Copying database",
    "write_test_db_config": "Updating wp-config.php",
    "plan_multisite_domain": "Planning Multisite test domains",
    "correct_multisite_domain": "Correcting and validating Multisite domains",
    "database_dump": "Exporting database",
    "database_import": "Importing database",
    "write_wp_config": "Updating wp-config.php",
    "pending_search_replace": "Search/replace simulation",
    "snapshot": "Saving widget snapshot",
    "core_update": "Updating WordPress core",
    "core_database_update": "Updating WordPress database",
    "managed_plugin_refresh": "Refreshing managed plugins",
    "third_party_plugin_update": "Updating plugins",
    "theme_update": "Updating themes",
    "core_languages": "Updating core translations",
    "plugin_languages": "Updating plugin translations",
    "theme_languages": "Updating theme translations",
    "widget_validation": "Final widget validation",
}

CAPABILITY_LABELS = {
    Capability.PHP_AVAILABLE: "PHP available",
    Capability.WPCLI_AVAILABLE: "WP-CLI available",
    Capability.DATABASE_AVAILABLE: "Database reachable",
    Capability.WP_CORE_DETECTED: "WordPress detected",
    Capability.WPCLI_FULL_BOOTSTRAP: "Full WP-CLI bootstrap",
}


class CompositeProgressReporter:
    def __init__(self, reporters: Iterable[ProgressReporter]) -> None:
        self._reporters = tuple(reporters)

    def run_started(self, manifest: RunManifest, total_steps: int) -> None:
        for reporter in self._reporters:
            reporter.run_started(manifest, total_steps)

    def capabilities_checked(self, stage: str, report: CapabilityReport) -> None:
        for reporter in self._reporters:
            reporter.capabilities_checked(stage, report)

    def step_started(self, name: str, index: int, total: int) -> None:
        for reporter in self._reporters:
            reporter.step_started(name, index, total)

    def step_finished(self, result: StepResult, index: int, total: int) -> None:
        for reporter in self._reporters:
            reporter.step_finished(result, index, total)

    def run_finished(self, manifest: RunManifest) -> None:
        for reporter in self._reporters:
            reporter.run_finished(manifest)

    def run_failed(self, manifest: RunManifest, reason: str) -> None:
        for reporter in self._reporters:
            reporter.run_failed(manifest, reason)


class TerminalProgressReporter:
    def __init__(self, operation: str | None = None) -> None:
        self._step_open = False
        self._operation = operation

    def run_started(self, manifest: RunManifest, total_steps: int) -> None:
        operation = self._operation or manifest.operation.value
        click.echo(f"{_operation_label(operation)}: {manifest.installation_id}")
        click.echo(f"Mode: {'DRY RUN' if manifest.dry_run else 'EXECUTE'}")
        click.echo()

    def capabilities_checked(self, stage: str, report: CapabilityReport) -> None:
        pass

    def step_started(self, name: str, index: int, total: int) -> None:
        label = STEP_LABELS.get(name, name.replace("_", " ").capitalize())
        prefix = f"[{index}/{total}] {label} "
        click.echo(prefix + "." * max(1, 44 - len(prefix)), nl=False)
        self._step_open = True

    def step_finished(self, result: StepResult, index: int, total: int) -> None:
        status = {
            StepStatus.EXECUTED: "OK",
            StepStatus.VALIDATED: "VALIDATED",
            StepStatus.PLANNED: "PLANNED",
            StepStatus.FAILED: "FAILED",
            StepStatus.SKIPPED: "SKIPPED",
        }.get(result.status, result.status.value)
        click.echo(f" {status}")
        self._step_open = False

    def run_finished(self, manifest: RunManifest) -> None:
        pass

    def run_failed(self, manifest: RunManifest, reason: str) -> None:
        if self._step_open:
            click.echo(" FAILED")
            self._step_open = False


def emit_diagnosis(report: dict[str, object], log_path: str) -> None:
    click.echo(f"Diagnosing {report['installation']}")
    click.echo(f"Path: {report['path']}")
    click.echo()
    capabilities = cast(list[object], report.get("capabilities", []))
    by_capability = {
        item["capability"]: item
        for item in capabilities
        if isinstance(item, dict) and "capability" in item
    }
    for capability, label in CAPABILITY_LABELS.items():
        result = by_capability.get(capability)
        if result is None:
            continue
        available = bool(result.get("available"))
        warning = capability is Capability.WPCLI_FULL_BOOTSTRAP and not available
        click.echo(f"{'✓' if available else '⚠' if warning else '✗'} {label}")
    click.echo()
    click.echo(f"Health: {report['health']}")
    fatal_errors = report.get("fatal_errors", ())
    if isinstance(fatal_errors, (list, tuple)):
        for error in fatal_errors:
            click.echo(f"Error: {Redactor().redact(_short_reason(str(error)))}")
    if report["health"] == HealthStatus.WPCLI_PARTIAL.value:
        click.echo("Warning: WordPress could only be loaded using reduced bootstrap.")
    click.echo()
    click.echo(f"Log: {log_path}")


def emit_run_summary(manifest: RunManifest, log_path: str, operation: str | None = None) -> None:
    click.echo()
    operation_label = _operation_label(operation or manifest.operation.value)
    if manifest.status is RunStatus.UPDATE_FAILED_PRESERVED:
        click.echo(f"{operation_label} stopped at: {manifest.failed_step}")
        click.echo("State preserved for recovery.")
        click.echo(f"Run ID: {manifest.run_id}")
        failed_step = next(
            (step for step in reversed(manifest.steps) if step.name == manifest.failed_step), None
        )
        if failed_step is not None:
            click.echo()
            reason = Redactor().redact(_short_reason(failed_step.message))
            click.echo(f"Reason: {reason}")
        click.echo()
        click.echo("See complete log:")
        click.echo(f"  {log_path}")
        return

    if manifest.dry_run:
        summarized_steps = manifest.steps
        if manifest.resumed_from_run_id is not None:
            summarized_steps = manifest.steps[
                next(
                    (
                        index
                        for index, step in enumerate(manifest.steps)
                        if step.status is not StepStatus.EXECUTED
                    ),
                    len(manifest.steps),
                ) :
            ]
        validated = sum(step.status is StepStatus.VALIDATED for step in summarized_steps)
        planned = sum(step.status is StepStatus.PLANNED for step in summarized_steps)
        failed_count = sum(step.status is StepStatus.FAILED for step in summarized_steps)
        click.echo("Dry run completed.")
        click.echo(
            f"{len(summarized_steps)} steps: {validated} validated, {planned} planned, "
            f"{failed_count} failed."
        )
    else:
        click.echo(f"{operation_label} completed successfully.")
        click.echo(f"Steps: {len(manifest.steps)} successful")
    click.echo(f"Run ID: {manifest.run_id}")
    click.echo(f"Installation: {manifest.installation_id}")
    click.echo(f"Status: {manifest.status.value}")
    if manifest.health_after is not None:
        click.echo(f"Health: {manifest.health_after.value}")
    click.echo(f"Log: {log_path}")


def _short_reason(reason: str, limit: int = 180) -> str:
    first_line = next(
        (line.strip() for line in reason.splitlines() if line.strip()), "Unknown error"
    )
    return first_line if len(first_line) <= limit else first_line[: limit - 1] + "…"


def _operation_label(operation: str) -> str:
    return {"migrate": "Migration"}.get(operation, operation.title())


def emit_plan(payload: dict[str, object]) -> None:
    import shlex

    routes = payload.get("modernization", {})
    if not isinstance(routes, dict):
        return
    for installation, route in routes.items():
        click.echo(f"Installation: {installation}")
        click.echo(f"Modernization class: {route['modernization_class']}")
        click.echo(f"Current WordPress: {route['initial_wordpress']}")
        click.echo(f"Policy: {route['policy_id']} revision {route['policy_revision']}")
        if route.get("runtimes"):
            click.echo("Configured PHP runtimes:")
            for runtime in route["runtimes"]:
                status = "installed" if runtime["installed"] else "missing/incompatible"
                click.echo(
                    f"  {runtime['name']}: {runtime['binary']} - {status} "
                    f"({runtime.get('detected_version') or 'not detected'})"
                )
        initial_php = route.get("initial_php")
        stages = route.get("stages", [])
        if initial_php and stages and initial_php != stages[0]["php"]:
            initial_runtime = initial_php.get("runtime")
            click.echo("Compatibility preflight:")
            click.echo(
                "  PHP runtime: "
                + (initial_runtime["binary"] if initial_runtime else "not configured")
            )
            click.echo(
                "  Status: "
                + ("available" if initial_php["satisfies_requirement"] else "missing/incompatible")
            )
        if route.get("reason_code"):
            click.echo(f"BLOCKED: {route['reason_code']}")
        if route.get("manual_target_wordpress"):
            click.echo(
                f"Automatic modernization starts at WordPress {route['manual_target_wordpress']}."
            )
            click.echo(
                "Use a compatible legacy environment for a manual bridge; "
                "run inventory/diagnose/plan again."
            )
        click.echo("Upgrade route:")
        for index, stage in enumerate(route["stages"], 1):
            selection = stage["php"]
            requirement = selection["requirement"]
            runtime = selection.get("runtime")
            required = (
                "current"
                if requirement["current"]
                else requirement["exact"] or f">= {requirement['minimum']}"
            )
            if requirement.get("maximum"):
                required += f" (compatible through {requirement['maximum']}.x)"
            click.echo(f"  {index}. WordPress {stage['target']} ({stage['wordpress']})")
            click.echo(f"     PHP: {required}")
            click.echo(f"     Runtime: {runtime['binary'] if runtime else 'not configured'}")
            status = "available" if selection["satisfies_requirement"] else "missing/incompatible"
            detected = runtime.get("detected_version") if runtime else None
            click.echo(f"     Status: {status} (detected: {detected or 'unknown'})")
    suggestions = payload.get("provisioning_suggestions", [])
    if isinstance(suggestions, list):
        for suggestion in suggestions:
            click.echo(f"Missing PHP runtime: {suggestion['runtime']}")
            click.echo(suggestion["guidance"])
            for key in ("install", "verify", "remove"):
                if suggestion[key]:
                    click.echo(f"  {key.title()}: {shlex.join(suggestion[key])}")
    click.echo("Execution readiness: " + ("READY" if payload.get("execution_ready") else "BLOCKED"))
