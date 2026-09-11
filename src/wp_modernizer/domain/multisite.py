"""Explicit, replayable domain changes; no inferred topology or identifier changes."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from urllib.parse import urlsplit, urlunsplit

from .errors import WordPressUnavailableError


def require(condition: bool, message: str) -> None:
    if not condition:
        raise WordPressUnavailableError(f"Multisite: {message}")


def valid_domain(value: str) -> bool:
    return (
        bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value))
        and all(
            0 < len(label) <= 63 and not label.startswith("-") and not label.endswith("-")
            for label in value.split(".")
        )
        and len(value) <= 253
    )


@dataclass(frozen=True)
class NetworkConfig:
    domain: str
    path: str
    network_id: int
    blog_id: int
    subdomains: bool


@dataclass(frozen=True)
class NetworkRow:
    id: int
    domain: str
    path: str


@dataclass(frozen=True)
class BlogRow:
    blog_id: int
    site_id: int
    domain: str
    path: str
    home: str
    siteurl: str


@dataclass(frozen=True)
class NetworkSnapshot:
    networks: tuple[NetworkRow, ...]
    blogs: tuple[BlogRow, ...]


def transform_network(
    config: NetworkConfig, snapshot: NetworkSnapshot, source_url: str, test_url: str
) -> NetworkSnapshot:
    source, target = urlsplit(source_url), urlsplit(test_url)
    old, new = source.hostname or "", target.hostname or ""
    require(valid_domain(old) and valid_domain(new) and old != new, "domínios ambíguos")
    require(
        source.scheme in {"http", "https"}
        and target.scheme == source.scheme
        and source.port is None
        and target.port is None
        and not source.username
        and not target.username
        and not source.query
        and not target.query
        and not source.fragment
        and not target.fragment
        and (source.path or "/").rstrip("/") == (target.path or "/").rstrip("/"),
        "mudança de protocolo, porta ou path não suportada nesta correção",
    )
    require(
        source_url.rstrip("/") not in test_url.rstrip("/") and not new.startswith(old + "."),
        "URLs sobrepostas impedem search-replace seguro",
    )
    require(config.domain in {old, new}, "DOMAIN_CURRENT_SITE diverge da origem/destino")
    require(len(snapshot.networks) == 1, "múltiplas redes ou tabela site vazia")
    network = snapshot.networks[0]
    require(
        network.id == config.network_id and network.path == config.path,
        "identificador/path da rede divergente",
    )
    require(network.domain == config.domain, "domínio da rede divergente")
    require(
        (source.path or "/").rstrip("/") == config.path.rstrip("/"),
        "URL de origem não identifica a raiz da rede",
    )
    require(bool(snapshot.blogs), "tabela blogs vazia")
    require(len({b.blog_id for b in snapshot.blogs}) == len(snapshot.blogs), "IDs duplicados")
    main = [b for b in snapshot.blogs if b.blog_id == config.blog_id]
    require(
        len(main) == 1 and main[0].domain == config.domain and main[0].path == config.path,
        "site principal ambíguo",
    )
    result = []
    for blog in snapshot.blogs:
        require(blog.blog_id > 0 and blog.site_id == network.id, "relação de rede inválida")
        require(
            blog.path.startswith(config.path)
            and blog.path.endswith("/")
            and "//" not in blog.path
            and ".." not in blog.path
            and not any(c in blog.path for c in "?#\\\r\n\x00"),
            "path inválido",
        )
        require(valid_domain(blog.domain), "domínio de blog inválido")
        require(
            (config.domain == new and (blog.domain == new or blog.domain.endswith("." + new)))
            or (
                config.domain == old
                and (blog.domain == old or blog.domain.endswith("." + old))
                and blog.domain != new
                and not blog.domain.endswith("." + new)
            ),
            "domínios mistos/ambíguos antes do planejamento",
        )
        if blog.domain == new or blog.domain.endswith("." + new):
            label = blog.domain[: -len(new)].rstrip(".")
        elif blog.domain == old or blog.domain.endswith("." + old):
            label = blog.domain[: -len(old)].rstrip(".")
        else:
            require(False, "domínio mapeado externo à rede")
            label = ""
        require(config.subdomains or not label, "subdomínio em rede de subdiretórios")
        require(
            not config.subdomains or blog.path == config.path, "path de subdomínio diverge da raiz"
        )
        domain = f"{label}.{new}" if label else new
        require(valid_domain(domain), "domínio final inválido")
        old_domain = f"{label}.{old}" if label else old
        urls = []
        for value in (blog.home, blog.siteurl):
            url = urlsplit(value)
            require(
                url.scheme == source.scheme
                and url.hostname in {old_domain, domain}
                and url.port is None
                and not url.username
                and not url.query
                and not url.fragment
                and (url.path or "/").rstrip("/") == blog.path.rstrip("/"),
                "home/siteurl divergente ou não literal",
            )
            urls.append(urlunsplit(url._replace(netloc=domain)))
        result.append(replace(blog, domain=domain, home=urls[0], siteurl=urls[1]))
    require(len({(b.domain, b.path) for b in result}) == len(result), "colisão entre sites")
    return NetworkSnapshot((replace(network, domain=new),), tuple(result))
