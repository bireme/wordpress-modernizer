"""Exact internal authorities and replayable HTTP -> HTTPS transformations."""

from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import urlsplit, urlunsplit

from .multisite import NetworkSnapshot, require, valid_domain


def https_url(value: str, domain: str) -> str:
    url = urlsplit(value)
    require(
        url.scheme in {"http", "https"}
        and url.netloc == domain
        and valid_domain(domain)
        and not url.query
        and not url.fragment
        and not any(c.isspace() or c in "\\\x00" for c in value),
        "HTTPS: URL estrutural inesperada",
    )
    return urlunsplit(url._replace(scheme="https"))


def https_snapshot(snapshot: NetworkSnapshot) -> NetworkSnapshot:
    return replace(
        snapshot,
        blogs=tuple(
            replace(
                blog,
                home=https_url(blog.home, blog.domain),
                siteurl=https_url(blog.siteurl, blog.domain),
            )
            for blog in snapshot.blogs
        ),
    )


def internal_http_pattern(domain: str) -> str:
    require(valid_domain(domain), "HTTPS: hostname inválido")
    # A hostname suffix, userinfo (@), or port (:) must never match this authority.
    # Case-insensitive matching is requested explicitly by the WP-CLI adapter.
    return r"http://" + re.escape(domain) + r"""(?=[/?#\s"'<>\)\]\}]|$)"""


def assert_https_replay(
    current: NetworkSnapshot, before: NetworkSnapshot, after: NetworkSnapshot
) -> None:
    require(
        current.networks == before.networks == after.networks,
        "HTTPS: estrutura de rede diverge do plano",
    )
    require(
        len(current.blogs) == len(before.blogs) == len(after.blogs),
        "HTTPS: sites adicionados/removidos",
    )
    for actual, old, new in zip(current.blogs, before.blogs, after.blogs, strict=True):
        require(
            replace(actual, home=old.home, siteurl=old.siteurl) == old
            and replace(new, home=old.home, siteurl=old.siteurl) == old
            and actual.home in {old.home, new.home}
            and actual.siteurl in {old.siteurl, new.siteurl},
            "HTTPS: estado inesperado; home/siteurl ou topologia diverge do plano",
        )
