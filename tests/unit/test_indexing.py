import copy
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from tests.fakes.core import FakeCommandResult, FakeCommandRunner
from tests.unit.test_https import fixture as https_fixture
from tests.unit.test_multisite import execute
from wp_modernizer.domain.enums import Environment
from wp_modernizer.domain.errors import UnsafeOperationError, WordPressUnavailableError
from wp_modernizer.infrastructure.runtime_operations import RuntimeOperations
from wp_modernizer.infrastructure.wp_config_writer import WordPressConfigWriter
from wp_modernizer.infrastructure.wpcli.adapter import WPCLIAdapter


def fixture(tmp_path, multisite=None, initial="1"):
    runtime, ctx, db, wp = https_fixture(tmp_path, multisite)
    execute(runtime, ctx, "plan_test_https")
    execute(runtime, ctx, "enforce_test_https")
    plan = json.loads(ctx["recovery_data"]["site"]["test_https_plan"])
    wp.options = {b["home"]: initial for b in plan["after"]["blogs"]}
    wp.option_writes = []
    wp.interrupt = False
    wp.ignore_write = False
    original = wp.update

    def update(path, args, run_id):
        if args[1] != "option":
            return original(path, args, run_id)
        assert args[0].startswith("--url=https://")
        assert args[3] == "blog_public"
        url = args[0].removeprefix("--url=")
        if args[2] == "get":
            return wp.options[url]
        assert args[2:] == ("update", "blog_public", "0")
        wp.option_writes.append(url)
        if not wp.ignore_write:
            wp.options[url] = "0"
        if wp.interrupt:
            raise WordPressUnavailableError("interrupção")
        return "Success"

    wp.update = update
    return runtime, ctx, db, wp


@pytest.mark.parametrize("multisite", [None, False, True])
@pytest.mark.parametrize("initial", ["1", "0", "-1", "mixed"])
def test_all_sites_idempotence_and_preservation(tmp_path, multisite, initial):
    runtime, ctx, _db, wp = fixture(tmp_path, multisite, "1" if initial == "mixed" else initial)
    if initial == "mixed":
        wp.options[next(iter(wp.options))] = "0"
    expected_changes = sum(v != "0" for v in wp.options.values())
    before = copy.deepcopy((wp.urls, wp.snapshot, wp.content, wp.values))
    config = (tmp_path / "wp-config.php").read_bytes()
    assert not execute(runtime, ctx, "plan_test_indexing").changed
    assert not wp.option_writes
    assert execute(runtime, ctx, "disable_test_indexing").changed == bool(expected_changes)
    assert len(wp.option_writes) == expected_changes
    assert set(wp.options.values()) == {"0"}
    assert not execute(runtime, ctx, "plan_test_indexing").changed
    assert not execute(runtime, ctx, "disable_test_indexing").changed
    assert len(wp.option_writes) == expected_changes
    assert (wp.urls, wp.snapshot, wp.content, wp.values) == before
    assert (tmp_path / "wp-config.php").read_bytes() == config
    state = ctx["recovery_data"]["site"]
    assert state["test_indexing_validated_count"] == str(len(wp.options))
    assert json.loads(state["test_indexing_plan"])["https"]["prefix"] == "custom_"


@pytest.mark.parametrize("multisite", [None, False, True])
def test_resume_after_write_before_checkpoint(tmp_path, multisite):
    runtime, ctx, db, wp = fixture(tmp_path, multisite)
    execute(runtime, ctx, "plan_test_indexing")
    saved = json.dumps(ctx["recovery_data"])
    wp.interrupt = True
    with pytest.raises(WordPressUnavailableError, match="interrupção"):
        execute(runtime, ctx, "disable_test_indexing")
    wp.interrupt = False
    ctx["recovery_data"] = json.loads(saved)
    if wp.snapshot:
        wp.snapshot = replace(wp.snapshot, blogs=tuple(reversed(wp.snapshot.blogs)))
    resumed = RuntimeOperations(Mock(), db, wp, Mock(), config_writer=WordPressConfigWriter())
    execute(resumed, ctx, "disable_test_indexing")
    assert len(wp.option_writes) == len(wp.options)
    assert set(wp.options.values()) == {"0"}
    assert ctx["recovery_data"]["site"]["test_indexing_validated"] == "true"


@pytest.mark.parametrize("value", ["", "2", "false", "0\nWarning", "01"])
def test_ambiguous_value_refused(tmp_path, value):
    runtime, ctx, _, wp = fixture(tmp_path, True, value)
    with pytest.raises(WordPressUnavailableError, match="inesperado"):
        execute(runtime, ctx, "plan_test_indexing")
    assert not wp.option_writes


@pytest.mark.parametrize("where", ["destination", "endpoint", "connection"])
@pytest.mark.parametrize("step", ["plan_test_indexing", "disable_test_indexing"])
def test_production_refused(tmp_path, where, step):
    runtime, ctx, db, wp = fixture(tmp_path)
    execute(runtime, ctx, "plan_test_indexing")
    if where == "destination":
        ctx["installation"].destination_environment = Environment.PRODUCTION
    elif where == "endpoint":
        db.get_database.return_value.environment = Environment.PRODUCTION
    else:
        wp.values["DB_HOST"] = "production"
    with pytest.raises((UnsafeOperationError, WordPressUnavailableError)):
        execute(runtime, ctx, step)
    assert not wp.option_writes


@pytest.mark.parametrize(
    "drift",
    [
        "domain",
        "path",
        "blog_id",
        "home",
        "removed",
        "list",
        "option",
        "prefix",
        "config",
        "plan",
        "https",
    ],
)
def test_resume_drift_fails_before_mutation(tmp_path, drift):
    runtime, ctx, _, wp = fixture(tmp_path, True)
    execute(runtime, ctx, "plan_test_indexing")
    state = ctx["recovery_data"]["site"]
    if drift in {"domain", "path", "blog_id", "home"}:
        value = 88 if drift == "blog_id" else "https://unexpected.example/"
        wp.snapshot = replace(
            wp.snapshot,
            blogs=(replace(wp.snapshot.blogs[0], **{drift: value}), *wp.snapshot.blogs[1:]),
        )
    elif drift == "removed":
        wp.snapshot = replace(wp.snapshot, blogs=wp.snapshot.blogs[1:])
    elif drift == "list":
        wp.bad_list = True
    elif drift == "option":
        wp.options[next(iter(wp.options))] = "-1"
    elif drift == "prefix":
        state["table_prefix"] = "other_"
    elif drift == "config":
        (tmp_path / "wp-config.php").write_text("<?php")
    elif drift == "https":
        state.pop("test_https_validated")
    else:
        state.pop("test_indexing_plan")
    with pytest.raises(WordPressUnavailableError):
        execute(runtime, ctx, "disable_test_indexing")
    assert not wp.option_writes
    assert "test_indexing_validated" not in state


@pytest.mark.parametrize("multisite", [None, False, True])
def test_failed_option_validation(tmp_path, multisite):
    runtime, ctx, _, wp = fixture(tmp_path, multisite)
    execute(runtime, ctx, "plan_test_indexing")
    wp.ignore_write = True
    with pytest.raises(WordPressUnavailableError, match="permanece indexável"):
        execute(runtime, ctx, "disable_test_indexing")
    assert "test_indexing_validated" not in ctx["recovery_data"]["site"]


def test_wpcli_option_contract_and_warnings(tmp_path):
    runner = FakeCommandRunner(
        [FakeCommandResult(stdout="0"), FakeCommandResult(stdout="0", stderr="Warning: failed")]
    )
    wp = WPCLIAdapter(runner)
    args = ("--url=https://test.example/sub/", "option", "get", "blog_public")
    assert wp.update(tmp_path, args, "run") == "0"
    assert runner.calls[-1][-len(args) :] == args
    with pytest.raises(WordPressUnavailableError):
        wp.update(tmp_path, args, "run")


def test_indexing_plan_durable_before_first_write(tmp_path):
    from tests.fakes.core import FakeClock, FakeFileSystem, FakeProbe, health
    from wp_modernizer.domain.enums import HealthStatus, Operation, RunStatus
    from wp_modernizer.domain.models import PlannedStep, RunManifest
    from wp_modernizer.infrastructure.state import JsonStateStore
    from wp_modernizer.pipeline.runner import PipelineRunner
    from wp_modernizer.pipeline.steps import OperationStep

    runtime, ctx, _, wp = fixture(tmp_path, True)
    store = JsonStateStore(tmp_path / "state")
    manifest = RunManifest(
        "run-1",
        "site",
        Operation.MIGRATE,
        RunStatus.PLANNED,
        "now",
        False,
        recovery_data=ctx["recovery_data"],
    )
    ctx["manifest"] = manifest
    steps = [
        PlannedStep(name, True, True, "validate", "replay", "site")
        for name in ("plan_test_indexing", "disable_test_indexing")
    ]
    manifest.planned_steps = steps
    original = wp.update

    def update(path, args, run_id):
        if args[1:3] == ("option", "update"):
            saved = store.load_manifest("site", "run-1")
            plan = json.loads(saved.recovery_data["site"]["test_indexing_plan"])
            assert len(plan["before"]) == len(wp.options)
            assert saved.steps[-1].name == "plan_test_indexing"
        return original(path, args, run_id)

    wp.update = update
    runner = PipelineRunner(
        FakeProbe([health(HealthStatus.HEALTHY)]), store, FakeFileSystem(), FakeClock()
    )
    result = runner.run(manifest, tmp_path, [OperationStep(s, runtime) for s in steps], ctx)
    assert result.status is RunStatus.EXECUTED
    saved = store.load_manifest("site", "run-1")
    assert saved.recovery_data["site"]["test_indexing_validated"] == "true"
    assert saved.recovery_data["site"]["test_indexing_validated_count"] == str(len(wp.options))


@pytest.mark.parametrize("fault", ["enumeration", "earlier_option", "unreadable"])
def test_final_validation_after_writes(tmp_path, fault):
    runtime, ctx, _, wp = fixture(tmp_path, True)
    execute(runtime, ctx, "plan_test_indexing")
    original = wp.update
    first = next(iter(wp.options))

    def update(path, args, run_id):
        result = original(path, args, run_id)
        if len(wp.option_writes) == len(wp.options):
            if fault == "enumeration":
                wp.bad_list = True
            elif fault == "earlier_option":
                wp.options[first] = "1"
            elif args[1:3] == ("option", "get"):
                raise WordPressUnavailableError("blog indisponível")
        return result

    wp.update = update
    with pytest.raises(WordPressUnavailableError):
        execute(runtime, ctx, "disable_test_indexing")
    assert "test_indexing_validated" not in ctx["recovery_data"]["site"]


def test_indexing_dry_run_does_not_bootstrap():
    from tests.fakes.core import FakeOperations
    from tests.unit.test_service import service
    from wp_modernizer.domain.enums import Operation, StepStatus

    operations = FakeOperations()
    result = service(operations=operations).execute(Operation.MIGRATE, "parent", dry_run=True)
    for name in ("plan_test_indexing", "disable_test_indexing"):
        assert name not in operations.calls
        assert all(s.status is StepStatus.PLANNED for s in result.steps if s.name == name)
