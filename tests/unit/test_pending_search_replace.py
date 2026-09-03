from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fakes.core import FakeClock, FakeFileSystem, FakeProbe, FakeStateStore, health
from wp_modernizer.domain.enums import (
    Environment,
    HealthStatus,
    Operation,
    PendingOperationType,
    RunStatus,
    StepCapability,
    StepStatus,
)
from wp_modernizer.domain.errors import UnsafeOperationError, WordPressUnavailableError
from wp_modernizer.domain.models import PendingOperation, PlannedStep, RunManifest
from wp_modernizer.infrastructure.runtime_operations import RuntimeOperations
from wp_modernizer.pipeline.runner import PipelineRunner
from wp_modernizer.pipeline.steps import OperationStep


class RecordingWordPress:
    def __init__(
        self,
        old_url: str = "https://boletin.bireme.org",
        *,
        multisite: bool = False,
        replacements: int = 3,
        fail: bool = False,
    ) -> None:
        self.old_url = old_url
        self.multisite = multisite
        self.replacements = replacements
        self.fail = fail
        self.search_calls: list[dict[str, Any]] = []

    def get_site_url(self, path: Path, run_id: str) -> str:
        del path, run_id
        return self.old_url

    def is_multisite(self, path: Path, run_id: str) -> bool:
        del path, run_id
        return self.multisite

    def search_replace(
        self,
        path: Path,
        old_url: str,
        new_url: str,
        *,
        dry_run: bool,
        multisite: bool,
        run_id: str,
    ) -> int:
        self.search_calls.append(
            {
                "path": path,
                "old_url": old_url,
                "new_url": new_url,
                "dry_run": dry_run,
                "multisite": multisite,
                "run_id": run_id,
            }
        )
        if self.fail:
            raise WordPressUnavailableError("credential-that-must-not-leak")
        return self.replacements


def operation(wordpress: RecordingWordPress) -> RuntimeOperations:
    return RuntimeOperations(
        SimpleNamespace(),
        SimpleNamespace(),
        wordpress,  # type: ignore[arg-type]
        SimpleNamespace(),
    )


def execution_context(
    *,
    test_url: str = "",
    environment: Environment = Environment.TEST,
    manifest: RunManifest | None = None,
) -> dict[str, Any]:
    pending = PendingOperation(
        PendingOperationType.SEARCH_REPLACE,
        {"organizational_domain": "bireme.org", "test_url": test_url},
        "transformar a URL depois da importação do banco de TESTE",
    )
    context: dict[str, Any] = {
        "run_id": "run-1",
        "installation": SimpleNamespace(
            destination_environment=environment,
            destination_path=Path("/test/htdocs"),
        ),
        "installations": {},
        "migration_plan": SimpleNamespace(pending_operations=(pending,)),
        "planned_step": PlannedStep("pending_search_replace", True, True, "", "", "site"),
    }
    if manifest is not None:
        manifest.pending_operations = [pending]
        context["manifest"] = manifest
    return context


def test_conventional_single_site_search_replace_records_safe_count() -> None:
    wordpress = RecordingWordPress(replacements=11)
    result = operation(wordpress).execute("pending_search_replace", execution_context())

    assert result.status is StepStatus.SUCCEEDED
    assert result.metrics == {"replacements": 11.0}
    assert result.message == "search-replace concluído: 11 substituições"
    assert wordpress.search_calls == [
        {
            "path": Path("/test/htdocs"),
            "old_url": "https://boletin.bireme.org",
            "new_url": "https://boletin.teste.bireme.org",
            "dry_run": False,
            "multisite": False,
            "run_id": "run-1",
        }
    ]


def test_explicit_test_url_override_has_precedence() -> None:
    wordpress = RecordingWordPress()
    operation(wordpress).execute(
        "pending_search_replace",
        execution_context(test_url="https://qa.example.org/wordpress"),
    )
    assert wordpress.search_calls[0]["new_url"] == "https://qa.example.org/wordpress"


def test_multisite_enables_network_search_replace() -> None:
    wordpress = RecordingWordPress(multisite=True)
    operation(wordpress).execute("pending_search_replace", execution_context())
    assert wordpress.search_calls[0]["multisite"] is True


def test_old_equals_new_fails_without_executing_search_replace() -> None:
    wordpress = RecordingWordPress(old_url="https://qa.example.org")
    result = operation(wordpress).execute(
        "pending_search_replace", execution_context(test_url="https://qa.example.org/")
    )
    assert result.status is StepStatus.FAILED
    assert wordpress.search_calls == []


def test_production_destination_is_rejected_before_wpcli_call() -> None:
    wordpress = RecordingWordPress()
    with pytest.raises(UnsafeOperationError, match="fora de TESTE"):
        operation(wordpress).execute(
            "pending_search_replace",
            execution_context(environment=Environment.PRODUCTION),
        )
    assert wordpress.search_calls == []


def test_explicit_production_url_is_rejected_without_wpcli_call() -> None:
    wordpress = RecordingWordPress()
    result = operation(wordpress).execute(
        "pending_search_replace",
        execution_context(test_url="https://boletin.bireme.org/other-path"),
    )
    assert result.status is StepStatus.FAILED
    assert "produção" in result.message
    assert wordpress.search_calls == []


def test_search_replace_failure_is_sanitized_and_fail_preserved() -> None:
    wordpress = RecordingWordPress(fail=True)
    operations = operation(wordpress)
    manifest = RunManifest("run-1", "site", Operation.PIPELINE, RunStatus.RUNNING, "now", False)
    context = execution_context(manifest=manifest)

    result = PipelineRunner(
        FakeProbe([health(HealthStatus.HEALTHY)]),
        FakeStateStore(),
        FakeFileSystem(),
        FakeClock(),
    ).run(
        manifest,
        Path("/test/htdocs"),
        [OperationStep(context["planned_step"], operations)],
        context,
    )

    assert result.status is RunStatus.UPDATE_FAILED_PRESERVED
    assert result.failed_step == "pending_search_replace"
    assert result.steps[-1].status is StepStatus.FAILED
    assert "credential-that-must-not-leak" not in result.steps[-1].message


def test_success_marks_pending_operation_complete() -> None:
    manifest = RunManifest("run-1", "site", Operation.PIPELINE, RunStatus.RUNNING, "now", False)
    wordpress = RecordingWordPress()
    context = execution_context(manifest=manifest)
    # Simula a desserialização: manifesto e plano têm objetos equivalentes, não idênticos.
    manifest.pending_operations = [
        PendingOperation(
            operation.operation_type,
            dict(operation.parameters),
            operation.reason,
            operation.completed,
        )
        for operation in manifest.pending_operations
    ]
    operation(wordpress).execute("pending_search_replace", context)
    assert manifest.pending_operations[0].completed is True


def test_native_dry_run_validates_without_completing_pending_operation() -> None:
    manifest = RunManifest("run-1", "site", Operation.PIPELINE, RunStatus.RUNNING, "now", True)
    wordpress = RecordingWordPress(replacements=7)
    context = execution_context(manifest=manifest)
    context["planned_step"] = PlannedStep(
        "pending_search_replace",
        True,
        True,
        "",
        "",
        "site",
        capability=StepCapability.MUTABLE_WITH_NATIVE_DRY_RUN,
    )

    result = operation(wordpress).validate("pending_search_replace", context)

    assert result.status is StepStatus.VALIDATED
    assert result.changed is False
    assert result.metrics == {"potential_replacements": 7.0}
    assert wordpress.search_calls[0]["dry_run"] is True
    assert manifest.pending_operations[0].completed is False


def test_native_dry_run_uses_remote_resolution_not_existing_test_url() -> None:
    wordpress = RecordingWordPress(old_url="https://boletin.teste.bireme.org")
    context = execution_context()
    context["recovery_data"] = {
        "site": {
            "source_url": "https://boletin.bireme.org",
            "test_url": "https://boletin.teste.bireme.org",
        }
    }
    context["planned_step"] = PlannedStep(
        "pending_search_replace",
        True,
        True,
        "",
        "",
        "site",
        capability=StepCapability.MUTABLE_WITH_NATIVE_DRY_RUN,
    )

    result = operation(wordpress).validate("pending_search_replace", context)

    assert result.status is StepStatus.VALIDATED
    assert wordpress.search_calls[0]["old_url"] == "https://boletin.bireme.org"
    assert wordpress.search_calls[0]["new_url"] == "https://boletin.teste.bireme.org"
