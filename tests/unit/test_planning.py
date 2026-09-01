from pathlib import Path

from wp_modernizer.domain.enums import Environment, PendingOperationType, StepCapability
from wp_modernizer.domain.models import PendingOperation
from wp_modernizer.domain.path_parser import InstallationPathParser
from wp_modernizer.domain.planning import MigrationPlanner


def test_parent_children_plan_is_deterministic_and_excludes_children() -> None:
    parser = InstallationPathParser([Path("/home/apps")])
    parent = parser.parse("/home/apps/example.org/wp-main/htdocs", "parent", Environment.TEST)
    child_b = parser.parse("/home/apps/example.org/wp-main/htdocs/z-child", "b", Environment.TEST)
    child_a = parser.parse("/home/apps/example.org/wp-main/htdocs/a-child", "a", Environment.TEST)
    plan = MigrationPlanner().build(
        "parent", Environment.PRODUCTION, "source", "db", [child_b, parent, child_a]
    )
    assert [item.installation_id for item in plan.installations] == ["parent", "a", "b"]
    parent_copy = next(
        step
        for step in plan.steps
        if step.installation_id == "parent" and step.name == "copy_files"
    )
    assert child_a.path in parent_copy.excludes and child_b.path in parent_copy.excludes


def test_multiple_nesting_levels_each_exclude_descendants() -> None:
    parser = InstallationPathParser([Path("/home/apps")])
    paths = [
        parser.parse("/home/apps/example.org/wp-main/htdocs", "p", Environment.TEST),
        parser.parse("/home/apps/example.org/wp-main/htdocs/a", "c", Environment.TEST),
        parser.parse("/home/apps/example.org/wp-main/htdocs/a/b", "g", Environment.TEST),
    ]
    plan = MigrationPlanner().build("p", Environment.TEST, "test-source", "db", paths)
    copies = {step.installation_id: step for step in plan.steps if step.name == "copy_files"}
    assert len(copies["p"].excludes) >= 4
    assert paths[2].path in copies["c"].excludes


def test_pending_search_replace_runs_after_test_database_is_prepared() -> None:
    parser = InstallationPathParser([Path("/home/apps")])
    installation = parser.parse("/home/apps/example.org/wp-main/htdocs", "site", Environment.TEST)
    pending = PendingOperation(
        PendingOperationType.SEARCH_REPLACE,
        {"organizational_domain": "bireme.org", "test_url": ""},
        "test",
    )
    plan = MigrationPlanner().build(
        "site",
        Environment.PRODUCTION,
        "source",
        "db",
        [installation],
        (pending,),
    )
    names = [step.name for step in plan.steps]
    assert names[-2:] == ["write_test_db_config", "pending_search_replace"]
    capabilities = {step.name: step.capability for step in plan.steps}
    assert capabilities["snapshot_source_database"] is StepCapability.READ_ONLY
    assert capabilities["copy_database"] is StepCapability.MUTABLE_WITHOUT_SAFE_DRY_RUN
    assert capabilities["pending_search_replace"] is StepCapability.MUTABLE_WITH_NATIVE_DRY_RUN
