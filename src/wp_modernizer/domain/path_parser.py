from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, Optional

from .enums import Environment
from .errors import UnsafeOperationError
from .models import WordPressInstallation

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class InstallationPathParser:
    """Analisa `/root/<domain>/wp-<instance>/htdocs[/nested...]` sem realizar E/S."""

    def __init__(self, allowed_app_roots: Iterable[Path]) -> None:
        roots = tuple(Path(root).resolve(strict=False) for root in allowed_app_roots)
        if not roots:
            raise UnsafeOperationError("É necessária ao menos uma raiz de aplicação permitida")
        self._roots = roots

    def parse(
        self, raw_path: str, installation_id: str, environment: Environment
    ) -> WordPressInstallation:
        if not raw_path or "\x00" in raw_path:
            raise UnsafeOperationError("O caminho da instalação está vazio ou contém NUL")
        raw = Path(raw_path)
        if not raw.is_absolute() or ".." in raw.parts:
            raise UnsafeOperationError(
                "O caminho da instalação deve ser absoluto e não conter travessia"
            )
        path = raw.resolve(strict=False)
        root = self._matching_root(path)
        relative = path.relative_to(root)
        if len(relative.parts) < 3:
            raise UnsafeOperationError("Esperado: <domain>/wp-<instance>/htdocs")
        domain, instance_dir, document_dir, *nested = relative.parts
        if document_dir != "htdocs" or not instance_dir.startswith("wp-"):
            raise UnsafeOperationError(
                "O caminho não corresponde à estrutura configurada do WordPress"
            )
        segments = (domain, instance_dir, *nested)
        if any(not _SAFE_SEGMENT.fullmatch(part) or part in {".", ".."} for part in segments):
            raise UnsafeOperationError("O caminho contém um segmento inseguro")
        instance_name = instance_dir[3:]
        if not instance_name:
            raise UnsafeOperationError("O nome da instância não pode estar vazio")
        docroot = root / domain / instance_dir / "htdocs"
        nested_path: Optional[Path] = Path(*nested) if nested else None
        return WordPressInstallation(
            installation_id=installation_id,
            path=path,
            app_root=root / domain / instance_dir,
            domain=domain,
            instance_name=instance_name,
            document_root=docroot,
            environment=environment,
            relative_nested_path=nested_path,
        )

    def assert_safe_destructive_target(
        self, path: Path, installation: WordPressInstallation, *, is_symlink: bool = False
    ) -> None:
        canonical = path.resolve(strict=False)
        if installation.environment is not Environment.TEST:
            raise UnsafeOperationError("Operações destrutivas são proibidas fora de TESTE")
        if is_symlink or os.path.islink(path):
            raise UnsafeOperationError("Destino destrutivo recusado por ser um link simbólico")
        if canonical != installation.path or canonical == installation.document_root.parent:
            raise UnsafeOperationError(
                "O destino destrutivo não corresponde exatamente à instalação"
            )
        self._matching_root(canonical)

    def _matching_root(self, path: Path) -> Path:
        matches = [root for root in self._roots if path == root or root in path.parents]
        if not matches:
            raise UnsafeOperationError("O caminho está fora das raízes de aplicação configuradas")
        return max(matches, key=lambda value: len(value.parts))
