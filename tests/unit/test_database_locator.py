from types import SimpleNamespace

import pytest

from wp_modernizer.domain.database import (
    DatabaseLocator,
    ProductionTestDatabaseNamingStrategy,
)
from wp_modernizer.domain.enums import Environment
from wp_modernizer.domain.errors import (
    AmbiguousDatabaseError,
    DatabaseNotFoundError,
    UnsafeOperationError,
)


class Reader:
    def __init__(self, values, environments=None):
        self.values = values
        self.environments = environments or {}

    def get_database(self, endpoint_id):
        return SimpleNamespace(environment=self.environments.get(endpoint_id, Environment.TEST))

    def list_schemas(self, endpoint_id):
        value = self.values[endpoint_id]
        if isinstance(value, Exception):
            raise value
        return value


def locator(values, environments=None):
    return DatabaseLocator(Reader(values, environments), ProductionTestDatabaseNamingStrategy())


def test_maps_production_convention_to_exact_test_name() -> None:
    found = locator({"test": {"wp_portal_tst"}}).locate("wp_portal_prod", [], ["test"])
    assert (found.endpoint_id, found.database_name) == ("test", "wp_portal_tst")


def test_override_has_absolute_precedence_over_convention_and_aliases() -> None:
    found = locator({"test": {"wp_portal_tst", "portal_alias", "exception_name"}}).locate(
        "wp_portal_prod",
        ["portal_alias"],
        ["test"],
        override="exception_name",
    )
    assert found.database_name == "exception_name"


def test_missing_conventional_database_requests_infrastructure_provisioning() -> None:
    with pytest.raises(DatabaseNotFoundError, match="infraestrutura precisa provisionar"):
        locator({"test": set()}).locate("wp_portal_prod", [], ["test"])


def test_missing_override_is_reported_explicitly() -> None:
    with pytest.raises(DatabaseNotFoundError, match=r"database_override 'missing'.*não existe"):
        locator({"test": {"wp_portal_tst"}}).locate(
            "wp_portal_prod", [], ["test"], override="missing"
        )


def test_multiple_exact_candidates_are_ambiguous() -> None:
    with pytest.raises(AmbiguousDatabaseError, match="AMBIGUOUS_DATABASE"):
        locator({"test": {"wp_portal_tst", "portal_alias"}}).locate(
            "wp_portal_prod", ["portal_alias"], ["test"]
        )


def test_same_candidate_on_multiple_endpoints_is_ambiguous() -> None:
    with pytest.raises(AmbiguousDatabaseError, match="AMBIGUOUS_DATABASE"):
        locator({"a": {"wp_portal_tst"}, "b": {"wp_portal_tst"}}).locate(
            "wp_portal_prod", [], ["a", "b"]
        )


def test_name_outside_convention_requires_override_or_alias() -> None:
    with pytest.raises(DatabaseNotFoundError, match="não segue wp_<name>_prod"):
        locator({"test": {"portal_tst"}}).locate("portal_production", [], ["test"])


def test_alias_is_an_exact_documented_alternative() -> None:
    found = locator({"test": {"legacy_portal_test"}}).locate(
        "portal_production", ["legacy_portal_test"], ["test"]
    )
    assert found.database_name == "legacy_portal_test"


def test_production_endpoint_is_never_accepted_as_destination() -> None:
    with pytest.raises(UnsafeOperationError, match="não é de TESTE"):
        locator(
            {"production": {"wp_portal_tst"}},
            {"production": Environment.PRODUCTION},
        ).locate("wp_portal_prod", [], ["production"])


@pytest.mark.parametrize("error", [TimeoutError("slow"), PermissionError("denied")])
def test_infrastructure_failures_are_not_misreported_as_not_found(error: Exception) -> None:
    with pytest.raises(type(error)):
        locator({"test": error}).locate("wp_portal_prod", [], ["test"])
