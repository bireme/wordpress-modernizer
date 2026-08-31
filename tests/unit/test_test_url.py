import pytest

from wp_modernizer.domain.errors import ConfigurationError, UnsafeOperationError
from wp_modernizer.domain.test_url import OrganizationalTestUrlPolicy


@pytest.fixture
def policy() -> OrganizationalTestUrlPolicy:
    return OrganizationalTestUrlPolicy("bireme.org")


def test_derives_conventional_site_url(policy: OrganizationalTestUrlPolicy) -> None:
    assert policy.resolve("https://boletin.bireme.org") == "https://boletin.teste.bireme.org"


def test_derives_url_without_site_name(policy: OrganizationalTestUrlPolicy) -> None:
    assert policy.resolve("https://bireme.org") == "https://teste.bireme.org"


def test_explicit_url_has_absolute_precedence(policy: OrganizationalTestUrlPolicy) -> None:
    assert (
        policy.resolve("https://boletin.bireme.org", "https://qa.example.org/wordpress")
        == "https://qa.example.org/wordpress"
    )


def test_path_is_preserved_without_contaminating_hostname(
    policy: OrganizationalTestUrlPolicy,
) -> None:
    assert (
        policy.resolve("https://boletin.bireme.org/wordpress")
        == "https://boletin.teste.bireme.org/wordpress"
    )


@pytest.mark.parametrize("url", ["not-a-url", "http://boletin.bireme.org", "https:///missing"])
def test_rejects_invalid_or_non_https_url(policy: OrganizationalTestUrlPolicy, url: str) -> None:
    with pytest.raises(ConfigurationError):
        policy.resolve(url)


def test_rejects_hostname_outside_explicit_organizational_boundary(
    policy: OrganizationalTestUrlPolicy,
) -> None:
    with pytest.raises(ConfigurationError, match="declare test_url"):
        policy.resolve("https://boletin.bireme.org.example.net")


@pytest.mark.parametrize(
    "destination",
    ["https://boletin.bireme.org/test", "https://boletin.bireme.org:8443/test"],
)
def test_never_uses_production_as_destination(
    policy: OrganizationalTestUrlPolicy, destination: str
) -> None:
    with pytest.raises(UnsafeOperationError, match="produção"):
        policy.resolve("https://boletin.bireme.org", destination)
