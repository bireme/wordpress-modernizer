from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict

from wp_modernizer.domain.enums import (
    HealthStatus,
    Operation,
    PendingOperationType,
    RunStatus,
    StepStatus,
)
from wp_modernizer.domain.models import (
    CapabilityReport,
    PendingOperation,
    RunManifest,
    StepResult,
)


class JsonStateStore:
    def __init__(self, root: Path) -> None:
        self._root = root

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
            filesystem_fingerprint=raw.get("filesystem_fingerprint"),
            finished_at=raw.get("finished_at"),
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
        return value

    @staticmethod
    def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
