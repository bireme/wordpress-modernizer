from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Mapping

from wp_modernizer.domain.errors import WordPressUnavailableError
from wp_modernizer.domain.multisite import NetworkConfig, require, valid_domain
from wp_modernizer.infrastructure.multisite_config import inspect_multisite


class WordPressConfigWriter:
    _NAMES = frozenset({"DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"})

    def inspect_https_config(self, path: Path) -> NetworkConfig | None:
        from wp_modernizer.infrastructure.ssh.source_config import _strip_php_comments

        clean = _strip_php_comments((path / "wp-config.php").read_text(encoding="utf-8"))
        require(
            not any(
                name in clean
                for name in ("WP_HOME", "WP_SITEURL", "WP_CONTENT_URL", "WP_CONTENT_DIR", "SUNRISE")
            ),
            "HTTPS: constantes de URL/roteamento não suportadas",
        )
        require(
            not any(
                (path / "wp-content" / name).exists()
                for name in ("db.php", "object-cache.php", "advanced-cache.php")
            ),
            "HTTPS: drop-in de cache/banco não suportado",
        )
        return self.inspect_multisite(path)

    def inspect_multisite(self, path: Path) -> NetworkConfig | None:
        config = inspect_multisite((path / "wp-config.php").read_text(encoding="utf-8"))
        if config is not None:
            require(
                not any(
                    (path / "wp-content" / name).exists()
                    for name in ("object-cache.php", "db.php", "advanced-cache.php")
                ),
                "drop-in de cache/banco exige isolamento explícito antes da correção",
            )
        return config

    def set_multisite_domain(self, path: Path, source: str, target: str, run_id: str) -> None:
        config = self.inspect_multisite(path)
        require(config is not None, "correção de domínio requer MULTISITE")
        assert config is not None
        require(
            valid_domain(target) and config.domain in {source, target},
            "domínio inseguro ou configuração alterada",
        )
        if config.domain == target:
            return
        self._set_config(path, {"DOMAIN_CURRENT_SITE": target}, frozenset({"DOMAIN_CURRENT_SITE"}))

    def set_config(self, path: Path, values: Mapping[str, str], run_id: str) -> None:
        self._set_config(path, values, self._NAMES)

    def _set_config(self, path: Path, values: Mapping[str, str], names: frozenset[str]) -> None:
        config_path = path / "wp-config.php"

        try:
            original = config_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise WordPressUnavailableError(
                "não foi possível ler wp-config.php para atualização"
            ) from exc

        updated = original

        for name, value in values.items():
            if name not in names:
                raise WordPressUnavailableError(f"configuração WordPress não autorizada: {name}")

            if "\n" in value or "\r" in value:
                raise WordPressUnavailableError(
                    f"o valor de configuração {name} contém quebra de linha insegura"
                )

            pattern = re.compile(
                rf"""(?i:define)\s*\(\s*(['"]){re.escape(name)}\1\s*,\s*(['"])(.*?)\2\s*\)\s*;"""
            )

            matches = list(pattern.finditer(updated))
            if len(matches) != 1:
                raise WordPressUnavailableError(
                    f"wp-config.php contém definição ausente ou ambígua para {name}"
                )

            escaped = value.replace("\\", "\\\\").replace("'", "\\'")
            replacement = f"define('{name}', '{escaped}');"

            match = matches[0]
            updated = updated[: match.start()] + replacement + updated[match.end() :]

        if updated == original:
            return
        mode = config_path.stat().st_mode

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=config_path.parent,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(updated)

            os.chmod(temporary_path, mode)
            temporary_path.replace(config_path)
        except OSError as exc:
            raise WordPressUnavailableError("não foi possível atualizar wp-config.php") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
