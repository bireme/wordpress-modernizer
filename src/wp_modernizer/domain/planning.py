from pathlib import Path
from typing import Iterable, Tuple

from .enums import Environment
from .models import MigrationPlan, PendingOperation, PlannedStep, WordPressInstallation


class MigrationPlanner:
    """Cria um plano estável em árvore; a cópia pai exclui todas as raízes descendentes."""

    def build(
        self,
        installation_id: str,
        source_environment: Environment,
        source_server: str,
        database_endpoint: str,
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
                )
            )
            for name in ("snapshot_source_database", "copy_database", "write_test_db_config"):
                steps.append(
                    PlannedStep(
                        name,
                        True,
                        True,
                        "ponto de controle mais estado inspecionado",
                        "repetir com segurança",
                        node.installation_id,
                    )
                )
        return MigrationPlan(
            installation_id=installation_id,
            source_environment=source_environment,
            destination_environment=Environment.TEST,
            source_server=source_server,
            database_endpoint=database_endpoint,
            installations=nodes,
            steps=tuple(steps),
            pending_operations=pending_operations,
        )
