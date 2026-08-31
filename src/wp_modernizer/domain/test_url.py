from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

from .errors import ConfigurationError, UnsafeOperationError


@dataclass(frozen=True)
class OrganizationalTestUrlPolicy:
    """Resolve URLs de TESTE dentro de uma fronteira DNS organizacional explícita."""

    organizational_domain: str
    test_label: str = "teste"

    def __post_init__(self) -> None:
        domain = self.organizational_domain.rstrip(".").lower()
        if not _is_dns_name(domain):
            raise ConfigurationError("organizational_domain deve ser um hostname DNS válido")
        if not _is_dns_label(self.test_label):
            raise ConfigurationError("test_label deve ser um label DNS válido")
        object.__setattr__(self, "organizational_domain", domain)

    def resolve(self, production_url: str, explicit_test_url: str | None = None) -> str:
        production = _parse_https_url(production_url, "URL de produção")
        if explicit_test_url is not None:
            destination = _parse_https_url(explicit_test_url, "test_url")
            self._assert_not_production(production, destination)
            return urlunsplit(destination)

        production_host = production.hostname or ""
        suffix = f".{self.organizational_domain}"
        if production_host == self.organizational_domain:
            test_host = f"{self.test_label}.{self.organizational_domain}"
        elif production_host.endswith(suffix):
            site_name = production_host[: -len(suffix)]
            test_host = f"{site_name}.{self.test_label}.{self.organizational_domain}"
        else:
            raise ConfigurationError(
                "o hostname de produção não pertence a organizational_domain; "
                "declare test_url explicitamente para esta exceção"
            )

        destination = production._replace(netloc=_netloc_with_host(production, test_host))
        self._assert_not_production(production, destination)
        return urlunsplit(destination)

    @staticmethod
    def _assert_not_production(production: SplitResult, destination: SplitResult) -> None:
        if production.hostname == destination.hostname:
            raise UnsafeOperationError("a URL de TESTE não pode usar o hostname de produção")


def _parse_https_url(value: str, field_name: str) -> SplitResult:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{field_name} inválida") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or not _is_dns_name(parsed.hostname)
    ):
        raise ConfigurationError(f"{field_name} deve ser uma URL HTTPS absoluta e válida")
    if parsed.query or parsed.fragment:
        raise ConfigurationError(f"{field_name} não deve conter query string ou fragmento")
    # A leitura antecipada torna explícita a validação de portas feita por urllib.
    del port
    return parsed._replace(scheme="https")


def _netloc_with_host(parsed: SplitResult, hostname: str) -> str:
    return f"{hostname}:{parsed.port}" if parsed.port is not None else hostname


def _is_dns_name(value: str) -> bool:
    return len(value) <= 253 and all(_is_dns_label(label) for label in value.split("."))


def _is_dns_label(value: str) -> bool:
    return (
        0 < len(value) <= 63
        and value[0].isalnum()
        and value[-1].isalnum()
        and all(character.isalnum() or character == "-" for character in value)
    )
