import logging

from wp_modernizer.infrastructure.time import SystemClock, UUIDGenerator
from wp_modernizer.observability.logging import Metrics, StructuredLogger
from wp_modernizer.security.redaction import Redactor


def test_structured_logging_redacts_and_metrics_accumulate(caplog) -> None:
    caplog.set_level(logging.INFO)
    StructuredLogger(Redactor(["private"])).event("step", run_id="r", password="private")
    assert "private" not in caplog.text
    assert "[REDACTED]" in caplog.text
    metrics = Metrics()
    metrics.add("runs_total")
    metrics.add("runs_total", 2)
    assert metrics.snapshot()["runs_total"] == 3


def test_clock_and_ids_are_well_formed() -> None:
    assert "+00:00" in SystemClock().now_iso()
    assert UUIDGenerator().new() != UUIDGenerator().new()
