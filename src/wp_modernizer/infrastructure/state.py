from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from wp_modernizer.domain.enums import (
    Capability,
    Environment,
    HealthStatus,
    ManagedPluginStatus,
    Operation,
    PendingOperationType,
    RunStatus,
    StepCapability,
    StepStatus,
)
from wp_modernizer.domain.errors import StateStoreUnavailableError
from wp_modernizer.domain.models import (
    CapabilityReport,
    ManagedPlugin,
    ManagedPluginResult,
    MigrationPlan,
    PendingOperation,
    PlannedStep,
    RunManifest,
    StepResult,
    WordPressInstallation,
)
from wp_modernizer.domain.widgets import WidgetOption, WidgetSnapshot


class JsonStateStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def preflight(self) -> None:
        """Comprova criação, escrita e leitura usando o mesmo padrão atômico do store."""
        probe = self._root / f".wp-modernizer-preflight-{uuid4().hex}.json"
        temporary = probe.with_suffix(".tmp")
        token = uuid4().hex
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            if not self._root.is_dir():
                raise NotADirectoryError(str(self._root))
            self._atomic_json(probe, {"token": token})
            payload = json.loads(probe.read_text(encoding="utf-8"))
            if payload != {"token": token}:
                raise OSError("conteúdo lido diverge do conteúdo gravado")
        except (OSError, ValueError) as exc:
            raise StateStoreUnavailableError(
                "state_directory não está acessível como diretório ou não permite "
                f"criação, escrita e leitura: {self._root}"
            ) from exc
        finally:
            with suppress(OSError):
                temporary.unlink()
            with suppress(OSError):
                probe.unlink()

    def create_run(self, manifest: RunManifest) -> None:
        directory = self._run_dir(manifest.installation_id, manifest.run_id)
        for child in ("checkpoints", "snapshots", "logs"):
            (directory / child).mkdir(parents=True, exist_ok=True)
        self.save_manifest(manifest)

    def save_manifest(self, manifest: RunManifest) -> None:
        path = self._run_dir(manifest.installation_id, manifest.run_id) / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_json(path, self._serialize(manifest))

    def load_manifest(self, installation_id: str, run_id: str) -> RunManifest:
        raw = json.loads((self._run_dir(installation_id, run_id) / "manifest.json").read_text())
        return RunManifest(
            run_id=raw["run_id"],
            installation_id=raw["installation_id"],
            operation=Operation(raw["operation"]),
            status=RunStatus(raw["status"]),
            started_at=raw["started_at"],
            dry_run=raw["dry_run"],
            steps=[
                StepResult(
                    item["name"],
                    StepStatus(item["status"]),
                    item["changed"],
                    item["message"],
                    item.get("metrics", {}),
                    item.get("installation_id"),
                )
                for item in raw.get("steps", [])
            ],
            pending_operations=[
                PendingOperation(
                    PendingOperationType(item["operation_type"]),
                    item["parameters"],
                    item["reason"],
                    item.get("completed", False),
                )
                for item in raw.get("pending_operations", [])
            ],
            last_successful_step=raw.get("last_successful_step"),
            failed_step=raw.get("failed_step"),
            health_before=HealthStatus(raw["health_before"]) if raw.get("health_before") else None,
            health_after=HealthStatus(raw["health_after"]) if raw.get("health_after") else None,
            wpcli_full_bootstrap=raw.get("wpcli_full_bootstrap", False),
            wpcli_reduced_bootstrap=raw.get("wpcli_reduced_bootstrap", False),
            fatal_errors=raw.get("fatal_errors", []),
            widget_diff=raw.get("widget_diff", []),
            widget_snapshot=self._deserialize_widget_snapshot(raw.get("widget_snapshot")),
            filesystem_fingerprint=raw.get("filesystem_fingerprint"),
            finished_at=raw.get("finished_at"),
            planned_steps=[
                self._deserialize_planned_step(item) for item in raw.get("planned_steps", [])
            ],
            migration_plan=self._deserialize_migration_plan(raw.get("migration_plan")),
            execution_parameters=raw.get("execution_parameters"),
            recovery_data=raw.get("recovery_data", {}),
            original_run_id=raw.get("original_run_id"),
            resumed_from_run_id=raw.get("resumed_from_run_id"),
            resume_source_failed_step=raw.get("resume_source_failed_step"),
            managed_plugins=[
                ManagedPlugin(
                    item["slug"],
                    item["repository"],
                    item["branch"],
                    item["strategy"],
                    item["dirty_policy"],
                )
                for item in raw.get("managed_plugins", [])
            ],
            managed_plugin_results=[
                ManagedPluginResult(
                    item["slug"],
                    item["repository"],
                    item["branch"],
                    item["strategy"],
                    item["dirty_policy"],
                    ManagedPluginStatus(item["status"]),
                    item["changed"],
                    item["message"],
                    item.get("revision"),
                )
                for item in raw.get("managed_plugin_results", [])
            ],
        )

    @staticmethod
    def _deserialize_planned_step(raw: Dict[str, Any]) -> PlannedStep:
        return PlannedStep(
            name=raw["name"],
            mutable=raw["mutable"],
            idempotent=raw["idempotent"],
            completion_probe=raw["completion_probe"],
            partial_recovery=raw["partial_recovery"],
            installation_id=raw["installation_id"],
            excludes=tuple(Path(item) for item in raw.get("excludes", [])),
            capability=(StepCapability(raw["capability"]) if raw.get("capability") else None),
            dry_run_requirements=tuple(
                Capability(item) for item in raw.get("dry_run_requirements", [])
            ),
        )

    @staticmethod
    def _deserialize_widget_snapshot(raw: Any) -> WidgetSnapshot | None:
        if raw is None:
            return None
        return WidgetSnapshot.from_options(
            WidgetOption(
                table=item["table"],
                name=item["name"],
                value=bytes.fromhex(item["value"]),
                autoload=item["autoload"],
            )
            for item in raw.get("options", [])
        )

    @classmethod
    def _deserialize_migration_plan(cls, raw: Any) -> MigrationPlan | None:
        if raw is None:
            return None
        installations = tuple(
            WordPressInstallation(
                installation_id=item["installation_id"],
                path=Path(item["path"]),
                app_root=Path(item["app_root"]),
                domain=item["domain"],
                instance_name=item["instance_name"],
                document_root=Path(item["document_root"]),
                environment=Environment(item["environment"]),
                relative_nested_path=Path(item["relative_nested_path"])
                if item.get("relative_nested_path")
                else None,
                parent_installation=item.get("parent_installation"),
                children=tuple(item.get("children", [])),
            )
            for item in raw.get("installations", [])
        )
        pending = tuple(
            PendingOperation(
                PendingOperationType(item["operation_type"]),
                item["parameters"],
                item["reason"],
                item.get("completed", False),
            )
            for item in raw.get("pending_operations", [])
        )
        return MigrationPlan(
            installation_id=raw["installation_id"],
            source_environment=Environment(raw["source_environment"]),
            destination_environment=Environment(raw["destination_environment"]),
            source_server=raw["source_server"],
            database_endpoint=raw.get("database_endpoint"),
            installations=installations,
            steps=tuple(cls._deserialize_planned_step(item) for item in raw.get("steps", [])),
            pending_operations=pending,
        )

    def save_checkpoint(
        self, installation_id: str, run_id: str, step: StepResult, health: CapabilityReport
    ) -> None:
        checkpoint_dir = self._run_dir(installation_id, run_id) / "checkpoints"
        sequence = len(list(checkpoint_dir.glob("*.json")))
        path = checkpoint_dir / f"{sequence:04d}-{step.name}.json"
        self._atomic_json(path, {"step": self._serialize(step), "health": self._serialize(health)})

    def _run_dir(self, installation_id: str, run_id: str) -> Path:
        return self._root / installation_id / "runs" / run_id

    @classmethod
    def _serialize(cls, value: Any) -> Any:
        if hasattr(value, "value"):
            return value.value
        if hasattr(value, "__dataclass_fields__"):
            return {key: cls._serialize(item) for key, item in asdict(value).items()}
        if isinstance(value, dict):
            return {key: cls._serialize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [cls._serialize(item) for item in value]
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, bytes):
            return value.hex()
        return value

    @staticmethod
    def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
