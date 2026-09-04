from pathlib import Path
from typing import Iterable, Tuple

from .enums import Capability, Environment, HealthStatus, PendingOperationType, StepCapability
from .models import MigrationPlan, PendingOperation, PlannedStep, WordPressInstallation


class MigrationPlanner:
    """Cria um plano estável em árvore; a cópia pai exclui todas as raízes descendentes."""

    def build(
        self,
        installation_id: str,
        source_environment: Environment,
        source_server: str,
        source_database_endpoint: str,
        installations: Iterable[WordPressInstallation],
        pending_operations: Tuple[PendingOperation, ...] = (),
    ) -> MigrationPlan:
        nodes = tuple(
            sorted(installations, key=lambda item: (len(item.path.parts), str(item.path)))
        )
        steps = []
        for node in nodes:
            descendants = tuple(
                child.path
                for child in nodes
                if child.path != node.path and node.path in child.path.parents
            )
            steps.append(
                PlannedStep(
                    name="backup_existing_test",
                    mutable=True,
                    idempotent=True,
                    completion_probe="o manifesto e o resumo de conteúdo da cópia existem",
                    partial_recovery="criar uma nova cópia de segurança imutável",
                    installation_id=node.installation_id,
                    capability=StepCapability.MUTABLE_WITHOUT_SAFE_DRY_RUN,
                )
            )
            steps.append(
                PlannedStep(
                    name="copy_files",
                    mutable=True,
                    idempotent=True,
                    completion_probe="os manifestos de cópia da origem e do destino coincidem",
                    partial_recovery=(
                        "repetir a cópia idempotente; raízes aninhadas permanecem excluídas"
                    ),
                    installation_id=node.installation_id,
                    excludes=(*descendants, Path("*.sql"), Path(".wp-modernizer")),
                    capability=StepCapability.MUTABLE_WITHOUT_SAFE_DRY_RUN,
                    allowed_health_regressions=frozenset(
                        {
                            HealthStatus.DATABASE_UNAVAILABLE,
                            HealthStatus.WPCLI_PARTIAL,
                        }
                    ),
                )
            )
            for name in ("snapshot_source_database", "copy_database", "write_test_db_config"):
                capability = (
                    StepCapability.READ_ONLY
                    if name == "snapshot_source_database"
                    else StepCapability.MUTABLE_WITHOUT_SAFE_DRY_RUN
                )
                steps.append(
                    PlannedStep(
                        name,
                        capability is not StepCapability.READ_ONLY,
                        True,
                        "ponto de controle mais estado inspecionado",
                        "repetir com segurança",
                        node.installation_id,
                        capability=capability,
                        dry_run_requirements=(
                            (Capability.DATABASE_AVAILABLE,)
                            if capability is StepCapability.READ_ONLY
                            else ()
                        ),
                    )
                )
        if any(
            operation.operation_type is PendingOperationType.SEARCH_REPLACE
            and not operation.completed
            for operation in pending_operations
        ):
            steps.append(
                PlannedStep(
                    name="pending_search_replace",
                    mutable=True,
                    idempotent=True,
                    completion_probe="a URL de origem não permanece no banco de TESTE",
                    partial_recovery="preservar a cópia e repetir o search-replace com WP-CLI",
                    installation_id=installation_id,
                    capability=StepCapability.MUTABLE_WITH_NATIVE_DRY_RUN,
                    dry_run_requirements=(
                        Capability.WPCLI_REDUCED_BOOTSTRAP,
                        Capability.DATABASE_AVAILABLE,
                    ),
                )
            )
        return MigrationPlan(
            installation_id=installation_id,
            source_environment=source_environment,
            destination_environment=Environment.TEST,
            source_server=source_server,
            source_database_endpoint=source_database_endpoint,
            installations=nodes,
            steps=tuple(steps),
            pending_operations=pending_operations,
        )
