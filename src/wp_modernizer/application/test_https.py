"""P1.2 orchestration through TEST-only existing infrastructure ports."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from wp_modernizer.application.ports import DatabasePort, WordPressConfigWriterPort, WordPressPort
from wp_modernizer.domain.enums import Environment
from wp_modernizer.domain.errors import UnsafeOperationError
from wp_modernizer.domain.https import (
    assert_https_replay,
    https_snapshot,
    https_url,
    internal_http_pattern,
)
from wp_modernizer.domain.multisite import (
    BlogRow,
    NetworkConfig,
    NetworkRow,
    NetworkSnapshot,
    require,
    valid_domain,
)


def snapshot_from_dict(value: dict[str, Any]) -> NetworkSnapshot:
    return NetworkSnapshot(
        tuple(NetworkRow(**n) for n in value["networks"]),
        tuple(BlogRow(**b) for b in value["blogs"]),
    )


class TestHttpsOperations:
    def __init__(
        self,
        databases: DatabasePort,
        wordpress: WordPressPort,
        config_writer: WordPressConfigWriterPort,
    ) -> None:
        self.db = databases
        self.wp = wordpress
        self.writer = config_writer

    def execute(self, name: str, path: Path, state: dict[str, str], run_id: str) -> int:
        require(
            all(
                state.get(key)
                for key in (
                    "target_database_endpoint",
                    "target_database",
                    "table_prefix",
                    "test_url",
                )
            ),
            "HTTPS: resolução durável de TESTE ausente",
        )
        endpoint, database, prefix = (
            state["target_database_endpoint"],
            state["target_database"],
            state["table_prefix"],
        )
        if self.db.get_database(endpoint).environment is not Environment.TEST:
            raise UnsafeOperationError("HTTPS: endpoint deve ser TESTE")
        config = self.writer.inspect_https_config(path)
        values = self.db.wordpress_configuration(endpoint, database)
        require(
            set(values) == {"DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"},
            "HTTPS: conexão de TESTE incompleta",
        )
        for key, value in {**values, "table_prefix": prefix}.items():
            require(
                self.wp.get_config(path, key, run_id) == value,
                "HTTPS: wp-config não corresponde ao banco de TESTE",
            )
        target = urlsplit(state["test_url"])
        domain = target.hostname or ""
        require(valid_domain(domain), "HTTPS: domínio de TESTE inválido")
        https_url(state["test_url"], domain)

        def inspect() -> NetworkSnapshot:
            if config is not None:
                return self.db.inspect_network(endpoint, database, prefix)
            urls = self.db.inspect_site_urls(endpoint, database, prefix)
            return NetworkSnapshot(
                (), (BlogRow(1, 0, domain, target.path or "/", urls["home"], urls["siteurl"]),)
            )

        current = inspect()
        config_data = asdict(config) if config else None
        if name == "plan_test_https" and not state.get("test_https_plan"):
            if config is not None:
                require(
                    state.get("multisite_validated") == "true"
                    and bool(state.get("multisite_plan")),
                    "HTTPS: P1.1 não validado",
                )
                structural = json.loads(state["multisite_plan"])
                expected = snapshot_from_dict(structural["after"])
                require(
                    https_snapshot(current) == https_snapshot(expected)
                    and config == replace(NetworkConfig(**structural["config"]), domain=domain),
                    "HTTPS: estrutura diverge do P1.1",
                )
            require(bool(current.blogs), "HTTPS: nenhum site")
            for blog in current.blogs:
                require(
                    blog.domain == domain
                    or (
                        config is not None
                        and config.subdomains
                        and blog.domain.endswith("." + domain)
                    ),
                    "HTTPS: domínio externo à rede de TESTE",
                )
            after = https_snapshot(current)
            payload = {
                "version": 1,
                "test_url": state["test_url"],
                "endpoint": endpoint,
                "database": database,
                "prefix": prefix,
                "config": config_data,
                "before": asdict(current),
                "after": asdict(after),
                "replacements": [
                    {
                        "old": "http://" + host,
                        "new": "https://" + host,
                        "pattern": internal_http_pattern(host),
                        "blog_ids": [b.blog_id for b in current.blogs if b.domain == host],
                    }
                    for host in sorted({b.domain for b in current.blogs})
                ],
            }
            state["test_https_plan"] = json.dumps(payload, sort_keys=True)
            return 0  # Runner persists this plan before enforce_test_https can execute.
        require(bool(state.get("test_https_plan")), "HTTPS: plano persistido ausente")
        payload = json.loads(state["test_https_plan"])
        require(
            payload["version"] == 1
            and payload["config"] == config_data
            and payload["endpoint"] == endpoint
            and payload["database"] == database
            and payload["prefix"] == prefix
            and payload["test_url"] == state["test_url"],
            "HTTPS: configuração diverge do plano",
        )
        before, after = snapshot_from_dict(payload["before"]), snapshot_from_dict(payload["after"])
        require(
            bool(before.blogs)
            and all(
                blog.domain == domain
                or (config is not None and config.subdomains and blog.domain.endswith("." + domain))
                for blog in before.blogs
            )
            and https_snapshot(before) == after,
            "HTTPS: plano inválido ou domínio externo",
        )
        assert_https_replay(current, before, after)
        expected_replacements = [
            {
                "old": "http://" + host,
                "new": "https://" + host,
                "pattern": internal_http_pattern(host),
                "blog_ids": [b.blog_id for b in before.blogs if b.domain == host],
            }
            for host in sorted({b.domain for b in before.blogs})
        ]
        require(
            payload["replacements"] == expected_replacements, "HTTPS: substituições divergentes"
        )
        if name == "plan_test_https":
            return 0
        changed = 0
        for item in payload["replacements"]:
            assert_https_replay(inspect(), before, after)
            count = self.wp.search_replace(
                path,
                item["pattern"],
                item["new"],
                dry_run=True,
                multisite=config is not None,
                run_id=run_id,
                regex=True,
            )
            require(count >= 0, "HTTPS: contagem inválida")
            if count:
                changed += self.wp.search_replace(
                    path,
                    item["pattern"],
                    item["new"],
                    dry_run=False,
                    multisite=config is not None,
                    run_id=run_id,
                    regex=True,
                )
        require(inspect() == after, "HTTPS: home/siteurl ou estrutura final divergente")
        if config is not None:
            rows = json.loads(
                self.wp.update(
                    path,
                    (
                        f"--url={https_url(state['test_url'], domain)}",
                        "site",
                        "list",
                        "--format=json",
                        "--fields=blog_id,site_id,domain,path",
                    ),
                    run_id,
                )
            )
            actual = [{k: str(v) for k, v in row.items()} for row in rows]
            expected_rows = [
                {
                    k: str(v)
                    for k, v in asdict(b).items()
                    if k in {"blog_id", "site_id", "domain", "path"}
                }
                for b in after.blogs
            ]
            require(
                sorted(actual, key=lambda r: r["blog_id"])
                == sorted(expected_rows, key=lambda r: r["blog_id"]),
                "HTTPS: wp site list diverge do plano",
            )
        for item in payload["replacements"]:
            require(
                self.wp.search_replace(
                    path,
                    item["pattern"],
                    item["new"],
                    dry_run=True,
                    multisite=config is not None,
                    run_id=run_id,
                    regex=True,
                )
                == 0,
                "HTTPS: referências HTTP internas restantes",
            )
        require(
            self.writer.inspect_https_config(path) == config and inspect() == after,
            "HTTPS: configuração/estrutura mudou após validação",
        )
        state["test_https_validated"] = "true"
        return changed
