from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Mapping

from wp_modernizer.domain.errors import WordPressUnavailableError


class WordPressConfigWriter:
    _NAMES = frozenset({"DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"})

    def set_config(self, path: Path, values: Mapping[str, str], run_id: str) -> None:
        config_path = path / "wp-config.php"

        try:
            original = config_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise WordPressUnavailableError(
                "não foi possível ler wp-config.php para atualização"
            ) from exc

        updated = original

        for name, value in values.items():
            if name not in self._NAMES:
                raise WordPressUnavailableError(f"configuração WordPress não autorizada: {name}")

            if "\n" in value or "\r" in value:
                raise WordPressUnavailableError(
                    f"o valor de configuração {name} contém quebra de linha insegura"
                )

            pattern = re.compile(
                rf"""define\(\s*(['"]){re.escape(name)}\1\s*,\s*(['"])(.*?)\2\s*\)\s*;"""
            )

            matches = list(pattern.finditer(updated))
            if len(matches) != 1:
                raise WordPressUnavailableError(
                    f"wp-config.php contém definição ausente ou ambígua para {name}"
                )

            escaped = value.replace("\\", "\\\\").replace("'", "\\'")
            replacement = f"define('{name}', '{escaped}');"

            updated = pattern.sub(replacement, updated, count=1)

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
