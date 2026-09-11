"""Conservative literal inspection without executing copied PHP."""

import re

from wp_modernizer.domain.multisite import NetworkConfig, require, valid_domain
from wp_modernizer.infrastructure.ssh.source_config import _strip_php_comments


def inspect_multisite(content: str) -> NetworkConfig | None:
    clean = _strip_php_comments(content)

    def constant(name: str, optional: bool = False) -> str | None:
        declarations = list(re.finditer(r'\b(?i:define)\s*\(\s*([\'"])' + name + r"\1\s*,", clean))
        if optional and not declarations:
            require(re.search(r"\b" + name + r"\b", clean) is None, f"{name} não literal")
            return None
        require(len(declarations) == 1, f"{name} ausente/ambíguo")
        match = declarations[0]
        # Only unconditional standalone definitions are accepted. Ignore strings when
        # checking braces; an unrelated ABSPATH guard elsewhere remains supported.
        prefix = clean[: match.start()]
        masked = re.sub(r"'[^']*'|\"[^\"]*\"", "''", prefix)
        require(masked.count("{") == masked.count("}"), f"{name} condicional")
        previous = re.split(r";|\}|<\?php", masked)[-1].strip()
        require(not previous, f"{name} condicional ou expressão não suportada")
        tail = re.match(r"\s*(.*?)\s*\)\s*;", clean[match.end() :], re.DOTALL)
        require(tail is not None, f"{name} inválido")
        assert tail is not None
        return tail[1]

    raw_enabled = constant("MULTISITE", optional=True)
    enabled = raw_enabled.lower() if raw_enabled is not None else None
    require(enabled in {None, "true", "false", "1", "0"}, "MULTISITE não literal")
    if enabled in {None, "false", "0"}:
        return None

    def literal(name: str) -> str:
        value = constant(name) or ""
        require(
            len(value) >= 2
            and value[0] in "'\""
            and value[-1] == value[0]
            and not any(c in value[1:-1] for c in "'\"\\$\r\n\x00"),
            f"{name} não literal seguro",
        )
        return value[1:-1]

    require(
        not any(
            name in clean
            for name in ("SUNRISE", "WP_HOME", "WP_SITEURL", "WP_CONTENT_DIR", "WP_CONTENT_URL")
        ),
        "roteamento/URLs sobrescritos por configuração não suportada",
    )
    domain, path = literal("DOMAIN_CURRENT_SITE"), literal("PATH_CURRENT_SITE")
    require(valid_domain(domain), "DOMAIN_CURRENT_SITE inseguro")
    require(
        path.startswith("/") and path.endswith("/") and ".." not in path,
        "PATH_CURRENT_SITE inseguro",
    )
    subdomains = (constant("SUBDOMAIN_INSTALL") or "").lower()
    require(subdomains in {"true", "false", "1", "0"}, "SUBDOMAIN_INSTALL ambíguo")
    ids = [constant(name) or "" for name in ("SITE_ID_CURRENT_SITE", "BLOG_ID_CURRENT_SITE")]
    require(
        all(re.fullmatch(r"[1-9][0-9]*", value) is not None for value in ids),
        "IDs da configuração inválidos",
    )
    return NetworkConfig(domain, path, int(ids[0]), int(ids[1]), subdomains in {"true", "1"})
