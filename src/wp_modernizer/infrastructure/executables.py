from __future__ import annotations

import shutil


class ShutilExecutableLocator:
    """Localiza executáveis sem iniciar processos."""

    def which(self, executable: str) -> str | None:
        return shutil.which(executable)
