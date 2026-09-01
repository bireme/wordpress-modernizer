from __future__ import annotations

from typing import Iterable, Set

from wp_modernizer.config.models import ApplicationConfig
from wp_modernizer.domain.enums import Capability, StepCapability
from wp_modernizer.domain.models import PlannedStep


def required_capabilities(
    config: ApplicationConfig,
    steps: Iterable[PlannedStep],
    *,
    dry_run: bool,
) -> Set[Capability]:
    """Deriva somente os executáveis alcançáveis pela execução solicitada."""
    selected_steps = tuple(steps)
    active_steps = tuple(
        step
        for step in selected_steps
        if not (dry_run and step.capability is StepCapability.MUTABLE_WITHOUT_SAFE_DRY_RUN)
    )
    if not active_steps:
        return set()
    required = {
        Capability.PHP_AVAILABLE,
        Capability.WPCLI_AVAILABLE,
        Capability.MYSQL_AVAILABLE,
    }
    for step in active_steps:
        if step.name == "copy_files":
            installation = config.installations[step.installation_id]
            server = config.servers[installation.source_server]
            if server.authentication == "key":
                required.update((Capability.SSH_AVAILABLE, Capability.RSYNC_AVAILABLE))
        elif step.name == "copy_database":
            required.add(Capability.MYSQLDUMP_AVAILABLE)
        elif step.name == "managed_plugin_refresh" and config.managed_plugins:
            required.add(Capability.GIT_AVAILABLE)
    return required
