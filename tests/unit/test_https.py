import json
import re
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.fakes.core import FakeCommandResult, FakeCommandRunner
from tests.unit.test_multisite import CONFIG, SOURCE, TARGET, execute, network
from tests.unit.test_pending_search_replace import execution_context
from wp_modernizer.domain.enums import Environment
from wp_modernizer.domain.errors import UnsafeOperationError, WordPressUnavailableError
from wp_modernizer.domain.https import internal_http_pattern
from wp_modernizer.domain.multisite import transform_network
from wp_modernizer.infrastructure.mysql.adapter import MySQLAdapter
from wp_modernizer.infrastructure.runtime_operations import RuntimeOperations
from wp_modernizer.infrastructure.wp_config_writer import WordPressConfigWriter
from wp_modernizer.infrastructure.wpcli.adapter import WPCLIAdapter


class ContentWordPress:
    """In-memory serialization-aware port fake, not a WP-CLI integration substitute."""

    def __init__(self, values, urls, snapshot=None):
        self.values, self.urls, self.snapshot = values, urls, snapshot
        self.content = {
            "custom_posts": '<img src="http://teste.example.org/image">',
            "custom_postmeta": "http://teste.example.org/meta",
            "custom_options": {"widget": ["http://teste.example.org/widget"]},
            "custom_plugin": "background-image:url(http://teste.example.org/bg)",
            "custom_termmeta": "http://teste.example.org/menu",
            "external": "http://external-service.example http://legacy-api.example "
            "http://cdn-third-party.example http://teste.example.org.external/a "
            "http://teste.example.org@external/a",
        }
        self.calls = []
        self.fail_after = None
        self.applied = 0
        self.leave_content = False
        self.bad_list = False

    def get_config(self, path, name, run_id):
        return self.values[name]

    def search_replace(self, path, old, new, *, dry_run, multisite, run_id, regex=False):
        assert regex
        self.calls.append((old, dry_run, multisite))
        count = 0

        def walk(value):
            nonlocal count
            if isinstance(value, str):
                result, found = re.subn(old, new, value, flags=re.I)
                count += found
                return result
            if isinstance(value, list):
                return [walk(v) for v in value]
            return {k: walk(v) for k, v in value.items()}

        content = walk(self.content)
        urls = walk(self.urls)
        snapshot = self.snapshot
        if snapshot:
            snapshot = replace(
                snapshot,
                blogs=tuple(
                    replace(b, home=walk(b.home), siteurl=walk(b.siteurl)) for b in snapshot.blogs
                ),
            )
        if not dry_run:
            self.urls, self.snapshot = urls, snapshot
            if not self.leave_content:
                self.content = content
            self.applied += 1
            if self.fail_after == self.applied:
                raise WordPressUnavailableError("interrupção após aplicação parcial")
        return count

    def update(self, path, args, run_id):
        assert args[1:3] == ("site", "list")
        if self.bad_list:
            return "[]"
        return json.dumps(
            [
                {
                    k: v
                    for k, v in asdict(b).items()
                    if k in {"blog_id", "site_id", "domain", "path"}
                }
                for b in self.snapshot.blogs
            ]
        )


def fixture(tmp_path, multisite=None, https=False):
    scheme = "https" if https else "http"
    text = "<?php"
    snapshot = None
    state = {
        "target_database_endpoint": "test",
        "target_database": "testdb",
        "table_prefix": "custom_",
        "test_url": f"{scheme}://teste.example.org/base/",
        "source_url": f"{scheme}://example.org/base/",
    }
    if multisite is not None:
        config, source = network(multisite)
        snapshot = transform_network(config, source, SOURCE, TARGET)
        snapshot = replace(
            snapshot,
            blogs=tuple(
                replace(
                    b,
                    home=b.home.replace("https:", scheme + ":"),
                    siteurl=b.siteurl.replace("https:", scheme + ":"),
                )
                for b in snapshot.blogs
            ),
        )
        text = CONFIG.replace("'example.org'", "'teste.example.org'")
        if multisite:
            text = text.replace("'SUBDOMAIN_INSTALL', false", "'SUBDOMAIN_INSTALL', true")
        state.update(
            multisite_plan=json.dumps({"after": asdict(snapshot), "config": asdict(config)}),
            multisite_validated="true",
        )
    (tmp_path / "wp-config.php").write_text(text)
    values = {
        "DB_HOST": "test-db",
        "DB_NAME": "testdb",
        "DB_USER": "testuser",
        "DB_PASSWORD": "testpass",
    }
    wp = ContentWordPress(
        {**values, "table_prefix": "custom_"},
        {"home": state["test_url"], "siteurl": state["test_url"] + "wp"},
        snapshot,
    )
    if snapshot:
        for blog in snapshot.blogs:
            wp.content[f"custom_{blog.blog_id}_posts"] = f"http://{blog.domain}/image"
    db = Mock()
    db.get_database.return_value = SimpleNamespace(environment=Environment.TEST)
    db.wordpress_configuration.return_value = values
    db.inspect_network.side_effect = lambda *args: wp.snapshot
    db.inspect_site_urls.side_effect = lambda *args: wp.urls
    runtime = RuntimeOperations(Mock(), db, wp, Mock(), config_writer=WordPressConfigWriter())
    context = execution_context()
    context["installation"].destination_path = tmp_path
    context["recovery_data"] = {"site": state}
    return runtime, context, db, wp


@pytest.mark.parametrize("multisite", [None, False, True])
@pytest.mark.parametrize("https", [False, True])
def test_https_content_options_topology_and_idempotence(tmp_path, multisite, https):
    runtime, ctx, db, wp = fixture(tmp_path, multisite, https)
    original_config = (tmp_path / "wp-config.php").read_text()
    original_snapshot = wp.snapshot
    external = wp.content["external"]
    assert not execute(runtime, ctx, "plan_test_https").changed
    assert not wp.calls
    payload = json.loads(ctx["recovery_data"]["site"]["test_https_plan"])
    assert payload["prefix"] == "custom_"
    assert len(payload["replacements"]) == (3 if multisite else 1)
    assert execute(runtime, ctx, "enforce_test_https").changed
    assert wp.content["external"] == external
    assert wp.content["custom_options"]["widget"] == ["https://teste.example.org/widget"]
    for key, value in wp.content.items():
        if key != "external":
            assert "http://" not in str(value)
    if multisite is None:
        assert wp.urls == {
            "home": "https://teste.example.org/base/",
            "siteurl": "https://teste.example.org/base/wp",
        }
        db.inspect_site_urls.assert_called_with("test", "testdb", "custom_")
    else:
        assert wp.snapshot.networks == original_snapshot.networks
        for old, new in zip(original_snapshot.blogs, wp.snapshot.blogs, strict=True):
            assert replace(new, home=old.home, siteurl=old.siteurl) == old
            assert new.home.startswith("https://") and new.siteurl.startswith("https://")
    assert (tmp_path / "wp-config.php").read_text() == original_config
    mutations = wp.applied
    assert not execute(runtime, ctx, "enforce_test_https").changed
    assert wp.applied == mutations
    assert ctx["recovery_data"]["site"]["test_https_validated"] == "true"


@pytest.mark.parametrize("partial", ["home", "site", "search"])
def test_resume_partial_uses_persisted_plan(tmp_path, partial):
    runtime, ctx, db, wp = fixture(tmp_path, True)
    execute(runtime, ctx, "plan_test_https")
    encoded = json.dumps(ctx["recovery_data"])
    if partial == "home":
        first = wp.snapshot.blogs[0]
        wp.snapshot = replace(
            wp.snapshot,
            blogs=(
                replace(first, home=first.home.replace("http:", "https:")),
                *wp.snapshot.blogs[1:],
            ),
        )
    elif partial == "site":
        first = wp.snapshot.blogs[0]
        wp.snapshot = replace(
            wp.snapshot,
            blogs=(
                replace(
                    first,
                    home=first.home.replace("http:", "https:"),
                    siteurl=first.siteurl.replace("http:", "https:"),
                ),
                *wp.snapshot.blogs[1:],
            ),
        )
    else:
        wp.fail_after = 1
        with pytest.raises(WordPressUnavailableError, match="parcial"):
            execute(runtime, ctx, "enforce_test_https")
        wp.fail_after = None
    ctx["recovery_data"] = json.loads(encoded)
    # Fresh coordinator, no in-memory progress from the interrupted process.
    resumed = RuntimeOperations(Mock(), db, wp, Mock(), config_writer=WordPressConfigWriter())
    execute(resumed, ctx, "enforce_test_https")
    assert all(b.home.startswith("https://") for b in wp.snapshot.blogs)
    assert (
        ctx["recovery_data"]["site"]["test_https_plan"]
        == json.loads(encoded)["site"]["test_https_plan"]
    )


@pytest.mark.parametrize(
    "drift", ["home", "domain", "path", "blog_id", "removed", "config", "prefix"]
)
def test_unexpected_resume_state_rejected_before_writes(tmp_path, drift):
    runtime, ctx, _db, wp = fixture(tmp_path, True)
    execute(runtime, ctx, "plan_test_https")
    if drift in {"home", "domain", "path", "blog_id"}:
        value = 42 if drift == "blog_id" else "http://unexpected.org"
        wp.snapshot = replace(
            wp.snapshot,
            blogs=(replace(wp.snapshot.blogs[0], **{drift: value}), *wp.snapshot.blogs[1:]),
        )
    elif drift == "removed":
        wp.snapshot = replace(wp.snapshot, blogs=wp.snapshot.blogs[1:])
    elif drift == "prefix":
        ctx["recovery_data"]["site"]["table_prefix"] = "other_"
    else:
        file = tmp_path / "wp-config.php"
        file.write_text(
            file.read_text().replace("'SUBDOMAIN_INSTALL', true", "'SUBDOMAIN_INSTALL', false")
        )
    with pytest.raises(WordPressUnavailableError):
        execute(runtime, ctx, "enforce_test_https")
    assert wp.applied == 0


@pytest.mark.parametrize("where", ["destination", "endpoint", "connection"])
def test_production_refused(tmp_path, where):
    runtime, ctx, db, wp = fixture(tmp_path)
    if where == "destination":
        ctx["installation"].destination_environment = Environment.PRODUCTION
    elif where == "endpoint":
        db.get_database.return_value.environment = Environment.PRODUCTION
    else:
        wp.values["DB_HOST"] = "production"
    with pytest.raises((UnsafeOperationError, WordPressUnavailableError)):
        execute(runtime, ctx, "plan_test_https")
    db.inspect_site_urls.assert_not_called()
    assert not wp.calls


@pytest.mark.parametrize("constant", ["WP_HOME", "WP_SITEURL", "WP_CONTENT_URL", "SUNRISE"])
def test_overrides_fail_closed_single_and_multisite(tmp_path, constant):
    runtime, ctx, db, wp = fixture(tmp_path)
    (tmp_path / "wp-config.php").write_text(
        f"<?php define('{constant}', 'http://teste.example.org');"
    )
    with pytest.raises(WordPressUnavailableError, match="constantes"):
        execute(runtime, ctx, "plan_test_https")
    db.inspect_site_urls.assert_not_called()
    assert not wp.calls


@pytest.mark.parametrize("bad", ["content", "enumeration", "home"])
def test_post_validation_fails_closed(tmp_path, bad):
    runtime, ctx, _db, wp = fixture(tmp_path, True if bad == "enumeration" else None)
    execute(runtime, ctx, "plan_test_https")
    wp.leave_content = bad == "content"
    wp.bad_list = bad == "enumeration"
    if bad == "home":
        wp.search_replace = Mock(return_value=0)
    with pytest.raises(WordPressUnavailableError):
        execute(runtime, ctx, "enforce_test_https")
    assert "test_https_validated" not in ctx["recovery_data"]["site"]


def test_missing_plan_and_already_https_without_content_changes(tmp_path):
    runtime, ctx, _db, wp = fixture(tmp_path, https=True)
    with pytest.raises(WordPressUnavailableError, match="persistido ausente"):
        execute(runtime, ctx, "enforce_test_https")
    wp.content = {"external": wp.content["external"]}
    execute(runtime, ctx, "plan_test_https")
    assert not execute(runtime, ctx, "plan_test_https").changed
    assert not execute(runtime, ctx, "enforce_test_https").changed
    assert wp.applied == 0


@pytest.mark.parametrize("suffix", ["/path", "?q=1", "#id", '"', "'", ")", "", " "])
def test_pattern_authority_boundaries(suffix):
    pattern = internal_http_pattern("teste.example.org")
    assert (
        re.sub(pattern, "https://teste.example.org", "http://teste.example.org" + suffix)
        == "https://teste.example.org" + suffix
    )
    for external in [
        "http://teste.example.org.external/path",
        "http://teste.example.org@external/path",
        "http://external-service.example",
        "http://teste.example.org:8080/path",
    ]:
        assert re.search(pattern, external) is None


def test_wpcli_regex_serialization_aware_contract(tmp_path):
    runner = FakeCommandRunner([FakeCommandResult(stdout="2"), FakeCommandResult(stdout="0")])
    wp = WPCLIAdapter(runner)
    pattern = internal_http_pattern("teste.example.org")
    for dry_run in (False, True):
        wp.search_replace(
            tmp_path,
            pattern,
            "https://teste.example.org",
            dry_run=dry_run,
            multisite=True,
            run_id="r",
            regex=True,
        )
        args = runner.calls[-1]
        for flag in (
            "--all-tables-with-prefix",
            "--precise",
            "--network",
            "--regex",
            "--regex-flags=i",
        ):
            assert flag in args
        assert ("--dry-run" in args) == dry_run
        assert "--all-tables" not in args
        assert pattern in args


def test_mysql_single_urls_reads_exact_values_and_custom_prefix():
    db = MySQLAdapter({}, Mock(), Mock())
    db.get_database = Mock(return_value=SimpleNamespace(environment=Environment.TEST))
    home, siteurl = "http://teste.example.org/", "https://teste.example.org/wp"
    db._query = Mock(return_value=f"home\t{home.encode().hex()}\nsiteurl\t{siteurl.encode().hex()}")
    assert db.inspect_site_urls("test", "test", "custom_") == {"home": home, "siteurl": siteurl}
    assert "`custom_options`" in db._query.call_args.args[1]
    for rows in ["", "home\t00", "home\t00\nhome\t00"]:
        db._query.return_value = rows
        with pytest.raises(WordPressUnavailableError):
            db.inspect_site_urls("test", "test", "custom_")
    with pytest.raises(WordPressUnavailableError):
        db.inspect_site_urls("test", "test", "bad`prefix")


def test_https_plan_is_durable_before_mutation(tmp_path):
    from tests.fakes.core import FakeClock, FakeFileSystem, FakeProbe, health
    from wp_modernizer.domain.enums import HealthStatus, Operation, RunStatus
    from wp_modernizer.domain.models import PlannedStep, RunManifest
    from wp_modernizer.infrastructure.state import JsonStateStore
    from wp_modernizer.pipeline.runner import PipelineRunner
    from wp_modernizer.pipeline.steps import OperationStep

    runtime, ctx, _db, wp = fixture(tmp_path)
    state = JsonStateStore(tmp_path / "state")
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
        for name in ("plan_test_https", "enforce_test_https")
    ]
    manifest.planned_steps = steps
    original = wp.search_replace

    def check(*args, **kwargs):
        saved = state.load_manifest("site", "run-1")
        assert saved.recovery_data["site"]["test_https_plan"]
        assert saved.steps[-1].name == "plan_test_https"
        return original(*args, **kwargs)

    wp.search_replace = check
    runner = PipelineRunner(
        FakeProbe([health(HealthStatus.HEALTHY)]), state, FakeFileSystem(), FakeClock()
    )
    result = runner.run(manifest, tmp_path, [OperationStep(s, runtime) for s in steps], ctx)
    assert result.status is RunStatus.EXECUTED
    assert (
        state.load_manifest("site", "run-1").recovery_data["site"]["test_https_validated"] == "true"
    )


def test_https_planning_dry_run_never_bootstraps_or_mutates():
    from tests.fakes.core import FakeOperations
    from tests.unit.test_service import service
    from wp_modernizer.domain.enums import Operation, StepStatus

    operations = FakeOperations()
    result = service(operations=operations).execute(Operation.MIGRATE, "parent", dry_run=True)
    for name in ("plan_test_https", "enforce_test_https"):
        assert name not in operations.calls
        assert all(s.status is StepStatus.PLANNED for s in result.steps if s.name == name)


def test_wpcli_https_warnings_are_nonfatal_when_command_succeeds(tmp_path):
    runner = FakeCommandRunner(
        [FakeCommandResult(stdout="47418", stderr="Warning: skipped object")]
    )

    result = WPCLIAdapter(runner).search_replace(
        tmp_path,
        internal_http_pattern("teste.example.org"),
        "https://teste.example.org",
        dry_run=True,
        multisite=False,
        run_id="r",
        regex=True,
    )

    assert result == 47418
