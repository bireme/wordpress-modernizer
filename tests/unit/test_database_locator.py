import pytest

from wp_modernizer.domain.database import DatabaseLocator, SuffixDatabaseNamingStrategy
from wp_modernizer.domain.errors import AmbiguousDatabaseError, DatabaseNotFoundError


class Reader:
    def __init__(self, values):
        self.values = values

    def list_schemas(self, endpoint_id):
        value = self.values[endpoint_id]
        if isinstance(value, Exception):
            raise value
        return value


def locator(values):
    return DatabaseLocator(Reader(values), SuffixDatabaseNamingStrategy("test"))


def test_locates_exactly_one_endpoint() -> None:
    found = locator({"a": {"site_test"}, "b": set()}).locate(
        "site_prod", [], ["a", "b"], {}, "site"
    )
    assert (found.endpoint_id, found.database_name) == ("a", "site_test")


def test_not_found_is_explicit() -> None:
    with pytest.raises(DatabaseNotFoundError):
        locator({"a": set()}).locate("site_prod", [], ["a"], {}, "site")


def test_ambiguous_never_selects_silently() -> None:
    with pytest.raises(AmbiguousDatabaseError, match="AMBIGUOUS_DATABASE"):
        locator({"a": {"site_test"}, "b": {"site_test"}}).locate(
            "site_prod", [], ["a", "b"], {}, "site"
        )


def test_override_replaces_strategy_candidates() -> None:
    found = locator({"a": {"exception_name", "site_test"}}).locate(
        "site_prod", [], ["a"], {"site": "exception_name"}, "site"
    )
    assert found.database_name == "exception_name"


@pytest.mark.parametrize("error", [TimeoutError("slow"), PermissionError("denied")])
def test_infrastructure_failures_are_not_misreported_as_not_found(error: Exception) -> None:
    with pytest.raises(type(error)):
        locator({"a": error}).locate("site_prod", [], ["a"], {}, "site")
