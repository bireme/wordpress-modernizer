import json
import logging
from pathlib import Path

from wp_modernizer.infrastructure.time import SystemClock, UUIDGenerator
from wp_modernizer.observability.logging import Metrics, StructuredLogger, create_execution_log
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


def test_execution_log_is_json_lines_and_redacts_nested_secrets(tmp_path: Path) -> None:
    execution = create_execution_log(tmp_path, "pipeline", "site", redactor=Redactor(["known"]))
    execution.structured.event(
        "command_result",
        password="plain-secret",
        stdout="token=abc123 known",
        config="define('DB_PASSWORD', 'wp-secret')",
        url="https://user:url-secret@example.invalid/path",
    )
    execution.close()

    payload = json.loads(execution.path.read_text())
    content = execution.path.read_text()
    assert payload["event"] == "command_result"
    assert payload["password"] == "[REDACTED]"
    for secret in ("plain-secret", "abc123", "known", "wp-secret", "url-secret"):
        assert secret not in content


def test_multiple_executions_create_separate_log_files(tmp_path: Path) -> None:
    first = create_execution_log(tmp_path, "diagnose", "site")
    second = create_execution_log(tmp_path, "diagnose", "site")
    first.close()
    second.close()
    assert first.path != second.path
    assert len(list((tmp_path / "logs").glob("*.log"))) == 2
