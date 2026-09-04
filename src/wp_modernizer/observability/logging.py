import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional, Sequence

from wp_modernizer.application.ports import CommandResult, CommandRunner
from wp_modernizer.domain.models import CapabilityReport, RunManifest, StepResult
from wp_modernizer.pipeline.progress import ProgressReporter
from wp_modernizer.security.redaction import Redactor


class StructuredLogger:
    def __init__(self, redactor: Redactor, logger: Optional[logging.Logger] = None) -> None:
        self._redactor = redactor
        self._logger = logger or logging.getLogger("wp_modernizer")

    def event(self, name: str, **fields: Any) -> None:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": name,
            **fields,
        }
        redacted = self._redactor.value(payload)
        self._logger.info(json.dumps(redacted, default=str, sort_keys=True))


@dataclass
class ExecutionLog:
    path: Path
    structured: StructuredLogger
    _logger: logging.Logger
    _handler: logging.Handler

    def close(self) -> None:
        self._handler.close()
        self._logger.removeHandler(self._handler)


def create_execution_log(
    state_directory: Path,
    operation: str,
    installation_id: str,
    *,
    run_id: str | None = None,
    redactor: Redactor | None = None,
) -> ExecutionLog:
    log_directory = state_directory / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H%M%S_%f")
    safe_operation = _safe_filename(operation)
    safe_installation = _safe_filename(installation_id)
    suffix = f"_{_safe_filename(run_id)}" if run_id else ""
    path = log_directory / f"{timestamp}_{safe_operation}_{safe_installation}{suffix}.log"
    logger = logging.getLogger(f"wp_modernizer.execution.{timestamp}.{id(path)}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return ExecutionLog(path, StructuredLogger(redactor or Redactor(), logger), logger, handler)


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "unknown"


class StructuredProgressReporter(ProgressReporter):
    def __init__(self, logger: StructuredLogger) -> None:
        self._logger = logger

    def run_started(self, manifest: RunManifest, total_steps: int) -> None:
        self._logger.event(
            "run_started",
            installation=manifest.installation_id,
            operation=manifest.operation.value,
            dry_run=manifest.dry_run,
            run_id=manifest.run_id,
            total_steps=total_steps,
        )

    def capabilities_checked(self, stage: str, report: CapabilityReport) -> None:
        for result in report.results:
            self._logger.event(
                "capability_result",
                stage=stage,
                capability=result.capability.value,
                available=result.available,
                detail=result.detail,
                health=report.health.value,
            )
        for error in report.fatal_errors:
            self._logger.event("capability_fatal_error", stage=stage, error=error)

    def step_started(self, name: str, index: int, total: int) -> None:
        self._logger.event("step_started", step=name, index=index, total=total)

    def step_finished(self, result: StepResult, index: int, total: int) -> None:
        self._logger.event(
            "step_finished",
            step=result.name,
            index=index,
            total=total,
            status=result.status.value,
            changed=result.changed,
            message=result.message,
            metrics=result.metrics,
            installation=result.installation_id,
        )

    def run_finished(self, manifest: RunManifest) -> None:
        self._logger.event("run_finished", manifest=asdict(manifest))

    def run_failed(self, manifest: RunManifest, reason: str) -> None:
        self._logger.event("run_failed", reason=reason, manifest=asdict(manifest))


_active_logger: ContextVar[StructuredLogger | None] = ContextVar("active_logger", default=None)


@contextmanager
def active_execution_logger(logger: StructuredLogger) -> Iterator[None]:
    token = _active_logger.set(logger)
    try:
        yield
    finally:
        _active_logger.reset(token)


class ObservedCommandRunner:
    """Adds redacted command events when a command-scoped logger is active."""

    def __init__(self, delegate: CommandRunner, redactor: Redactor | None = None) -> None:
        self._delegate = delegate
        self._redactor = redactor or Redactor()

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Optional[Path] = None,
        timeout: float = 60,
        environment: Optional[Mapping[str, str]] = None,
        stdin_path: Optional[Path] = None,
        stdout_path: Optional[Path] = None,
        correlation_id: Optional[str] = None,
    ) -> CommandResult:
        result = self._delegate.run(
            argv,
            cwd=cwd,
            timeout=timeout,
            environment=environment,
            stdin_path=stdin_path,
            stdout_path=stdout_path,
            correlation_id=correlation_id,
        )
        logger = _active_logger.get()
        if logger is not None:
            logger.event(
                "command_result",
                argv=self._redactor.argv(tuple(result.argv)),
                cwd=str(cwd) if cwd else None,
                environment=self._redactor.mapping(environment or {}),
                return_code=result.return_code,
                stdout=self._redactor.redact(result.stdout),
                stderr=self._redactor.redact(result.stderr),
                elapsed_seconds=result.elapsed_seconds,
                correlation_id=result.correlation_id,
            )
        return result


class Metrics:
    """Métricas em processo sem dependências; uma ponte OTEL pode consumir instantâneos."""

    def __init__(self) -> None:
        self._counters: Dict[str, float] = {}

    def add(self, name: str, value: float = 1.0) -> None:
        self._counters[name] = self._counters.get(name, 0.0) + value

    def snapshot(self) -> Dict[str, float]:
        return dict(self._counters)
