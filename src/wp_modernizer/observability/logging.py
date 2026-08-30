import json
import logging
from typing import Any, Dict, Optional

from wp_modernizer.security.redaction import Redactor


class StructuredLogger:
    def __init__(self, redactor: Redactor, logger: Optional[logging.Logger] = None) -> None:
        self._redactor = redactor
        self._logger = logger or logging.getLogger("wp_modernizer")

    def event(self, name: str, **fields: Any) -> None:
        payload: Dict[str, Any] = {"event": name, **fields}
        self._logger.info(self._redactor.redact(json.dumps(payload, default=str, sort_keys=True)))


class Metrics:
    """Métricas em processo sem dependências; uma ponte OTEL pode consumir instantâneos."""

    def __init__(self) -> None:
        self._counters: Dict[str, float] = {}

    def add(self, name: str, value: float = 1.0) -> None:
        self._counters[name] = self._counters.get(name, 0.0) + value

    def snapshot(self) -> Dict[str, float]:
        return dict(self._counters)
