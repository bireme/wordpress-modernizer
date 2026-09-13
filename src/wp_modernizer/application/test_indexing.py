"""Persisted TEST-only WordPress search-engine discouragement (P1.3)."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from wp_modernizer.application.ports import DatabasePort, WordPressConfigWriterPort, WordPressPort
from wp_modernizer.application.test_https import snapshot_from_dict
from wp_modernizer.domain.enums import Environment
from wp_modernizer.domain.errors import UnsafeOperationError
from wp_modernizer.domain.https import https_snapshot
from wp_modernizer.domain.multisite import require


class TestIndexingOperations:
    def __init__(
        self, db: DatabasePort, wp: WordPressPort, writer: WordPressConfigWriterPort
    ) -> None:
        self.db, self.wp, self.writer = db, wp, writer

    def execute(self, name: str, path: Path, state: dict[str, str], run_id: str) -> int:
        state.pop("test_indexing_validated", None)
        state.pop("test_indexing_validated_count", None)
        require(
            state.get("test_https_validated") == "true" and bool(state.get("test_https_plan")),
            "Indexação: plano HTTPS validado ausente",
        )
        https = json.loads(state["test_https_plan"])
        require(
            https["version"] == 1
            and all(
                state.get(key) == https[field]
                for key, field in (
                    ("target_database_endpoint", "endpoint"),
                    ("target_database", "database"),
                    ("table_prefix", "prefix"),
                    ("test_url", "test_url"),
                )
            ),
            "Indexação: destino diverge do plano HTTPS",
        )
        endpoint, database, prefix = https["endpoint"], https["database"], https["prefix"]
        if self.db.get_database(endpoint).environment is not Environment.TEST:
            raise UnsafeOperationError("Indexação: endpoint deve ser TESTE")
        values = self.db.wordpress_configuration(endpoint, database)
        require(
            set(values) == {"DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"},
            "Indexação: conexão de TESTE incompleta",
        )
        for key, value in {**values, "table_prefix": prefix}.items():
            require(
                self.wp.get_config(path, key, run_id) == value,
                "Indexação: wp-config diverge do banco de TESTE",
            )
        expected = snapshot_from_dict(https["after"])
        domain = urlsplit(state["test_url"]).hostname
        require(
            bool(domain)
            and all(
                b.domain == domain
                or (
                    https["config"] is not None
                    and https["config"]["subdomains"]
                    and b.domain.endswith("." + str(domain))
                )
                for b in expected.blogs
            ),
            "Indexação: domínio externo ao TESTE",
        )
        blogs = {str(b.blog_id): b for b in expected.blogs}
        require(
            bool(blogs)
            and len(blogs) == len(expected.blogs)
            and https_snapshot(expected) == expected,
            "Indexação: sites/URLs HTTPS inválidos",
        )

        def inspect() -> None:
            config = self.writer.inspect_https_config(path)
            require(
                (asdict(config) if config else None) == https["config"],
                "Indexação: configuração HTTPS diverge",
            )
            if config is None:
                require(
                    len(blogs) == 1 and not expected.networks, "Indexação: single-site inválido"
                )
                blog = expected.blogs[0]
                require(
                    self.db.inspect_site_urls(endpoint, database, prefix)
                    == {"home": blog.home, "siteurl": blog.siteurl},
                    "Indexação: URLs divergentes",
                )
                return
            require(state.get("multisite_validated") == "true", "Indexação: P1.1 ausente")
            current = self.db.inspect_network(endpoint, database, prefix)
            require(
                sorted(current.blogs, key=lambda b: b.blog_id)
                == sorted(expected.blogs, key=lambda b: b.blog_id)
                and current.networks == expected.networks,
                "Indexação: topologia diverge do plano",
            )
            rows = json.loads(
                self.wp.update(
                    path,
                    (
                        f"--url={expected.blogs[0].home}",
                        "site",
                        "list",
                        "--format=json",
                        "--fields=blog_id,site_id,domain,path",
                    ),
                    run_id,
                )
            )
            actual = [{k: str(v) for k, v in row.items()} for row in rows]
            wanted = [
                {
                    k: str(v)
                    for k, v in asdict(b).items()
                    if k in {"blog_id", "site_id", "domain", "path"}
                }
                for b in expected.blogs
            ]
            require(
                sorted(actual, key=lambda r: r["blog_id"])
                == sorted(wanted, key=lambda r: r["blog_id"]),
                "Indexação: wp site list diverge da rede esperada",
            )

        def read(blog_id: str) -> str:
            value = self.wp.update(
                path, (f"--url={blogs[blog_id].home}", "option", "get", "blog_public"), run_id
            ).strip()
            # WordPress also uses -1 for private sites; do not interpret arbitrary output.
            require(value in {"-1", "0", "1"}, "Indexação: blog_public inesperado")
            return value

        inspect()
        if name == "plan_test_indexing" and not state.get("test_indexing_plan"):
            state["test_indexing_plan"] = json.dumps(
                {"version": 1, "https": https, "before": {key: read(key) for key in blogs}},
                sort_keys=True,
            )
            return 0  # Runner saves RunManifest before the first option update.
        require(bool(state.get("test_indexing_plan")), "Indexação: plano persistido ausente")
        plan = json.loads(state["test_indexing_plan"])
        require(
            plan["version"] == 1
            and plan["https"] == https
            and set(plan["before"]) == set(blogs)
            and all(v in {"-1", "0", "1"} for v in plan["before"].values()),
            "Indexação: plano divergente",
        )
        current = {key: read(key) for key in blogs}
        require(
            all(value in {plan["before"][key], "0"} for key, value in current.items()),
            "Indexação: estado parcial inesperado",
        )
        if name == "plan_test_indexing":
            return 0
        changed = 0
        for key in sorted(blogs):
            inspect()
            value = read(key)
            require(value in {plan["before"][key], "0"}, "Indexação: estado mudou")
            if value != "0":
                self.wp.update(
                    path,
                    (f"--url={blogs[key].home}", "option", "update", "blog_public", "0"),
                    run_id,
                )
                changed += 1
            require(read(key) == "0", "Indexação: blog permanece indexável")
        inspect()
        require(all(read(key) == "0" for key in blogs), "Indexação: validação final falhou")
        state["test_indexing_validated"] = "true"
        state["test_indexing_validated_count"] = str(len(blogs))
        return changed
