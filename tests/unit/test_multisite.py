import json
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.unit.test_pending_search_replace import execution_context
from wp_modernizer.domain.enums import Environment, StepStatus
from wp_modernizer.domain.errors import UnsafeOperationError, WordPressUnavailableError
from wp_modernizer.domain.models import PlannedStep
from wp_modernizer.domain.multisite import (
    BlogRow,
    NetworkConfig,
    NetworkRow,
    NetworkSnapshot,
    transform_network,
)
from wp_modernizer.infrastructure.multisite_config import inspect_multisite
from wp_modernizer.infrastructure.mysql.adapter import MySQLAdapter
from wp_modernizer.infrastructure.runtime_operations import RuntimeOperations
from wp_modernizer.infrastructure.wp_config_writer import WordPressConfigWriter

SOURCE = "https://example.org/base/"
TARGET = "https://teste.example.org/base/"
CONFIG = """<?php
// Existing network topology.
define('MULTISITE', true);
define('SUBDOMAIN_INSTALL', false);
define('DOMAIN_CURRENT_SITE', 'example.org');
define('PATH_CURRENT_SITE', '/base/');
define('SITE_ID_CURRENT_SITE', 7);
define('BLOG_ID_CURRENT_SITE', 1);
"""


def network(subdomains=False):
    config = NetworkConfig("example.org", "/base/", 7, 1, subdomains)
    blogs = []
    for blog_id, label in [(1, ""), (4, "site1"), (9, "site2")]:
        domain = f"{label}.example.org" if subdomains and label else "example.org"
        path = "/base/" + (label + "/" if label and not subdomains else "")
        url = f"https://{domain}{path}"
        blogs.append(BlogRow(blog_id, 7, domain, path, url, url.rstrip("/")))
    return config, NetworkSnapshot((NetworkRow(7, config.domain, config.path),), tuple(blogs))


@pytest.mark.parametrize("subdomains", [False, True])
def test_domain_mapping_preserves_topology_and_is_idempotent(subdomains):
    config, before = network(subdomains)
    after = transform_network(config, before, SOURCE, TARGET)
    assert after.networks == (NetworkRow(7, "teste.example.org", "/base/"),)
    for old, new in zip(before.blogs, after.blogs, strict=True):
        assert (old.blog_id, old.site_id, old.path) == (new.blog_id, new.site_id, new.path)
        assert new.domain == old.domain.replace("example.org", "teste.example.org")
        assert new.home == f"https://{new.domain}{new.path}"
        assert new.siteurl == new.home.rstrip("/")
    assert (
        transform_network(replace(config, domain="teste.example.org"), after, SOURCE, TARGET)
        == after
    )


@pytest.mark.parametrize(
    "content",
    [
        CONFIG.replace("define('DOMAIN_CURRENT_SITE', 'example.org');", ""),
        CONFIG + "define('DOMAIN_CURRENT_SITE', 'example.org');",
        CONFIG + "define('DOMAIN_CURRENT_SITE', getenv('DOMAIN'));",
        CONFIG.replace("'example.org'", "getenv('DOMAIN')"),
        CONFIG.replace("'example.org'", "'evil\\n.org'"),
        CONFIG.replace("define('MULTISITE', true)", "define('MULTISITE', getenv('MULTI'))"),
        CONFIG.replace("define('DOMAIN", "if (true) define('DOMAIN"),
        CONFIG.replace("define('DOMAIN", "if (true) { define('DOMAIN") + "}",
        CONFIG + "define('SUNRISE', true);",
    ],
)
def test_config_fail_closed(content):
    with pytest.raises(WordPressUnavailableError):
        inspect_multisite(content)


@pytest.mark.parametrize(
    "content",
    [
        "<?php",
        "<?php // MULTISITE true",
        CONFIG.replace("define('MULTISITE', true);", "define('MULTISITE', false);"),
    ],
)
def test_single_site_unchanged(tmp_path, content):
    file = tmp_path / "wp-config.php"
    file.write_text(content)
    writer = WordPressConfigWriter()
    assert writer.inspect_multisite(tmp_path) is None
    context = execution_context()
    context["installation"].destination_path = tmp_path
    context["planned_step"] = PlannedStep("plan_multisite_domain", True, True, "", "", "site")
    databases = Mock()
    runtime = RuntimeOperations(Mock(), databases, Mock(), Mock(), config_writer=writer)
    result = runtime.execute("plan_multisite_domain", context)
    assert not result.changed
    assert file.read_text() == content
    databases.inspect_network.assert_not_called()


def test_writer_atomic_permissions_authorization_and_repeat(tmp_path):
    file = tmp_path / "wp-config.php"
    file.write_text(CONFIG)
    file.chmod(0o640)
    writer = WordPressConfigWriter()
    writer.set_multisite_domain(tmp_path, "example.org", "teste.example.org", "r")
    assert file.read_text() == CONFIG.replace("'example.org'", "'teste.example.org'")
    assert file.stat().st_mode & 0o777 == 0o640
    inode = file.stat().st_ino
    writer.set_multisite_domain(tmp_path, "example.org", "teste.example.org", "r")
    assert file.stat().st_ino == inode
    with pytest.raises(WordPressUnavailableError):
        writer.set_config(tmp_path, {"DOMAIN_CURRENT_SITE": "other.org"}, "r")
    with pytest.raises(WordPressUnavailableError):
        writer.set_multisite_domain(tmp_path, "teste.example.org", "bad/host", "r")


@pytest.mark.parametrize(
    "source,target",
    [
        ("", TARGET),
        (SOURCE, ""),
        (SOURCE, SOURCE),
        (SOURCE, "https://other.org/changed/"),
        (SOURCE, "https://example.org.test/base/"),
        (SOURCE, "http://teste.example.org/base/"),
        (SOURCE, "https://teste.example.org:90/base/"),
    ],
)
def test_ambiguous_urls_rejected(source, target):
    config, snapshot = network()
    with pytest.raises(WordPressUnavailableError):
        transform_network(config, snapshot, source, target)


@pytest.mark.parametrize(
    "change",
    [
        lambda s: replace(s, networks=()),
        lambda s: replace(s, networks=s.networks * 2),
        lambda s: replace(s, blogs=()),
        lambda s: replace(s, blogs=(replace(s.blogs[0], site_id=5), *s.blogs[1:])),
        lambda s: replace(s, blogs=(replace(s.blogs[0], home="a:1:{serialized}"), *s.blogs[1:])),
        lambda s: replace(s, blogs=(*s.blogs[:2], replace(s.blogs[2], domain="mapped.org"))),
        lambda s: replace(s, blogs=(*s.blogs[:2], replace(s.blogs[2], path="/other/"))),
        lambda s: replace(s, blogs=s.blogs * 2),
    ],
)
def test_ambiguous_topology_rejected(change):
    config, snapshot = network()
    with pytest.raises(WordPressUnavailableError):
        transform_network(config, change(snapshot), SOURCE, TARGET)


def runtime_fixture(tmp_path):
    (tmp_path / "wp-config.php").write_text(CONFIG)
    config, before = network()
    after = transform_network(config, before, SOURCE, TARGET)
    db = Mock()
    db.wordpress_configuration.return_value = {"DB_HOST": "test-db", "DB_NAME": "test"}
    db.inspect_network.return_value = before
    db.apply_network.side_effect = lambda *args: setattr(db.inspect_network, "return_value", after)
    wp = Mock()
    wp.get_config.side_effect = lambda path, name, run: {
        "DB_HOST": "test-db",
        "DB_NAME": "test",
        "table_prefix": "custom_",
    }[name]
    wp.update.return_value = json.dumps(
        [
            {k: v for k, v in asdict(b).items() if k in {"blog_id", "site_id", "domain", "path"}}
            for b in after.blogs
        ]
    )
    runtime = RuntimeOperations(Mock(), db, wp, Mock(), config_writer=WordPressConfigWriter())
    context = execution_context()
    context["installation"].destination_path = tmp_path
    context["recovery_data"] = {
        "site": {
            "source_url": SOURCE,
            "test_url": TARGET,
            "target_database_endpoint": "test",
            "target_database": "test",
            "table_prefix": "custom_",
        }
    }
    return runtime, context, db, wp


def execute(runtime, context, name):
    context["planned_step"] = PlannedStep(name, True, True, "", "", "site")
    return runtime.execute(name, context)


def test_runtime_persisted_plan_replay_and_original_search_replace(tmp_path):
    runtime, context, db, wp = runtime_fixture(tmp_path)
    execute(runtime, context, "plan_multisite_domain")
    db.apply_network.assert_not_called()
    # JSON roundtrip is the persisted RunManifest recovery_data representation.
    context["recovery_data"] = json.loads(json.dumps(context["recovery_data"]))
    assert execute(runtime, context, "correct_multisite_domain").changed
    assert not execute(runtime, context, "correct_multisite_domain").changed
    wp.get_site_url.side_effect = AssertionError("must use original persisted URL")
    wp.is_multisite.return_value = True
    wp.search_replace.return_value = 2
    result = execute(runtime, context, "pending_search_replace")
    assert result.status is StepStatus.SUCCEEDED
    assert wp.search_replace.call_args.args[1:3] == (SOURCE, TARGET)
    assert wp.update.call_args.args[1][0] == f"--url={TARGET}"


def test_partial_apply_can_resume_after_database_was_changed(tmp_path):
    runtime, context, db, _wp = runtime_fixture(tmp_path)
    execute(runtime, context, "plan_multisite_domain")
    config, before = network()
    db.inspect_network.return_value = transform_network(config, before, SOURCE, TARGET)
    assert execute(runtime, context, "correct_multisite_domain").changed
    assert "teste.example.org" in (tmp_path / "wp-config.php").read_text()


def test_runtime_rejects_production_and_unverified_connection(tmp_path):
    runtime, context, db, wp = runtime_fixture(tmp_path)
    context["installation"].destination_environment = Environment.PRODUCTION
    with pytest.raises(UnsafeOperationError):
        execute(runtime, context, "plan_multisite_domain")
    context["installation"].destination_environment = Environment.TEST
    wp.get_config.return_value = "production"
    wp.get_config.side_effect = None
    with pytest.raises(WordPressUnavailableError, match="banco de TESTE"):
        execute(runtime, context, "plan_multisite_domain")
    db.apply_network.assert_not_called()
    db.inspect_network.assert_not_called()


def test_enumeration_must_match_all_ids_and_paths(tmp_path):
    runtime, context, _db, wp = runtime_fixture(tmp_path)
    execute(runtime, context, "plan_multisite_domain")
    wp.update.return_value = "[]"
    with pytest.raises(WordPressUnavailableError, match="site list"):
        execute(runtime, context, "correct_multisite_domain")
    assert (tmp_path / "wp-config.php").exists()


@pytest.mark.parametrize("name", ["object-cache.php", "db.php", "advanced-cache.php"])
def test_unknown_cache_database_routing_rejected(tmp_path, name):
    (tmp_path / "wp-config.php").write_text(CONFIG)
    (tmp_path / "wp-content").mkdir()
    (tmp_path / "wp-content" / name).touch()
    with pytest.raises(WordPressUnavailableError, match="drop-in"):
        WordPressConfigWriter().inspect_multisite(tmp_path)


def mysql_adapter():
    db = MySQLAdapter({}, Mock(), Mock())
    db.get_database = Mock(return_value=SimpleNamespace(environment=Environment.TEST))
    return db


def test_mysql_inspection_custom_prefix_and_missing_tables():
    db = mysql_adapter()
    config, before = network()

    def h(s):
        return s.encode().hex()

    outputs = [
        "custom_site\ncustom_blogs\ncustom_options\ncustom_4_options\ncustom_9_options",
        f"7\t{h(config.domain)}\t{h(config.path)}",
        "\n".join(f"{b.blog_id}\t7\t{h(b.domain)}\t{h(b.path)}" for b in before.blogs),
    ]
    outputs += [f"home\t{h(b.home)}\nsiteurl\t{h(b.siteurl)}" for b in before.blogs]
    db._query = Mock(side_effect=outputs)
    assert db.inspect_network("test", "test", "custom_") == before
    assert "custom_9_options" in db._query.call_args.args[1]
    db._query = Mock(return_value="custom_options")
    with pytest.raises(WordPressUnavailableError, match="tabelas"):
        db.inspect_network("test", "test", "custom_")
    with pytest.raises(WordPressUnavailableError, match="prefixo"):
        db.inspect_network("test", "test", "evil`;")


def test_mysql_exact_updates_partial_resume_and_idempotence():
    db = mysql_adapter()
    config, before = network(True)
    after = transform_network(config, before, SOURCE, TARGET)
    partial = replace(before, networks=after.networks, blogs=(after.blogs[0], *before.blogs[1:]))
    db.inspect_network = Mock(side_effect=[partial, after])
    db._execute_script = Mock()
    db.apply_network("test", "test", "custom_", before, after, "r")
    sql = db._execute_script.call_args.args[2]
    assert "REPLACE(" not in sql and "DELETE" not in sql
    assert "SET `blog_id`" not in sql and "SET `path`" not in sql and "SET `site_id`" not in sql
    assert "`custom_4_options`" in sql and "AND BINARY" in sql
    db.inspect_network = Mock(return_value=after)
    db._execute_script.reset_mock()
    db.apply_network("test", "test", "custom_", before, after, "r")
    db._execute_script.assert_not_called()
    db.get_database.return_value.environment = Environment.PRODUCTION
    with pytest.raises(UnsafeOperationError):
        db.apply_network("test", "test", "custom_", before, after, "r")


def test_atomic_failure_preserves_original_config(tmp_path, monkeypatch):
    file = tmp_path / "wp-config.php"
    file.write_text(CONFIG)

    def fail(*args):
        raise OSError("simulated atomic rename failure")

    monkeypatch.setattr(type(file), "replace", fail)
    with pytest.raises(WordPressUnavailableError):
        WordPressConfigWriter().set_multisite_domain(
            tmp_path, "example.org", "teste.example.org", "r"
        )
    assert file.read_text() == CONFIG
    assert list(tmp_path.iterdir()) == [file]


def test_allow_multisite_does_not_enable_network():
    assert inspect_multisite("<?php define('WP_ALLOW_MULTISITE', true);") is None


def test_mysql_drift_rejected_before_any_update():
    db = mysql_adapter()
    config, before = network()
    after = transform_network(config, before, SOURCE, TARGET)
    db.inspect_network = Mock(
        return_value=replace(
            before,
            blogs=(*before.blogs[:2], replace(before.blogs[2], siteurl="https://unexpected.org/")),
        )
    )
    db._execute_script = Mock()
    with pytest.raises(WordPressUnavailableError, match="diverge do plano"):
        db.apply_network("test", "test", "custom_", before, after, "r")
    db._execute_script.assert_not_called()


def test_plan_is_durable_before_first_mutation(tmp_path):
    from tests.fakes.core import FakeClock, FakeFileSystem, FakeProbe, health
    from wp_modernizer.domain.enums import HealthStatus, Operation, RunStatus
    from wp_modernizer.domain.models import RunManifest
    from wp_modernizer.infrastructure.state import JsonStateStore
    from wp_modernizer.pipeline.runner import PipelineRunner
    from wp_modernizer.pipeline.steps import OperationStep

    runtime, context, db, _wp = runtime_fixture(tmp_path)
    state = JsonStateStore(tmp_path / "state")
    manifest = RunManifest(
        "run-1",
        "site",
        Operation.MIGRATE,
        RunStatus.PLANNED,
        "now",
        False,
        recovery_data=context["recovery_data"],
    )
    context["manifest"] = manifest
    steps = [
        PlannedStep(name, True, True, "validate network", "replay plan", "site")
        for name in ("plan_multisite_domain", "correct_multisite_domain")
    ]
    manifest.planned_steps = steps
    config, before = network()
    after = transform_network(config, before, SOURCE, TARGET)

    def apply(*args):
        saved = state.load_manifest("site", "run-1")
        assert saved.recovery_data["site"]["multisite_plan"]
        assert saved.steps[-1].name == "plan_multisite_domain"
        db.inspect_network.return_value = after

    db.apply_network.side_effect = apply
    runner = PipelineRunner(
        FakeProbe([health(HealthStatus.HEALTHY)]), state, FakeFileSystem(), FakeClock()
    )
    result = runner.run(manifest, tmp_path, [OperationStep(s, runtime) for s in steps], context)
    assert result.status is RunStatus.EXECUTED
    assert (
        state.load_manifest("site", "run-1").recovery_data["site"]["multisite_validated"] == "true"
    )


def test_replanning_before_mutation_is_idempotent(tmp_path):
    runtime, context, db, _wp = runtime_fixture(tmp_path)
    execute(runtime, context, "plan_multisite_domain")
    plan = context["recovery_data"]["site"]["multisite_plan"]
    assert not execute(runtime, context, "plan_multisite_domain").changed
    assert context["recovery_data"]["site"]["multisite_plan"] == plan
    db.apply_network.assert_not_called()


def test_config_supports_php_boolean_case_and_spacing(tmp_path):
    file = tmp_path / "wp-config.php"
    file.write_text(
        CONFIG.replace("true", "TRUE").replace("false", "FALSE").replace("define(", "DEFINE (")
    )
    writer = WordPressConfigWriter()
    writer.set_multisite_domain(tmp_path, "example.org", "teste.example.org", "r")
    assert writer.inspect_multisite(tmp_path).domain == "teste.example.org"
