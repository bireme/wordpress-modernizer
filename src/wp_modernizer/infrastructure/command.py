from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Mapping, Optional, Sequence

from wp_modernizer.application.ports import CommandResult
from wp_modernizer.domain.errors import CommandTimeoutError, InfrastructureError
from wp_modernizer.security.redaction import Redactor


class SubprocessCommandRunner:
    """Único local de produção que invoca subprocessos; o shell está sempre desabilitado."""

    def __init__(self, redactor: Optional[Redactor] = None) -> None:
        self._redactor = redactor or Redactor()

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Optional[Path] = None,
        timeout: float = 60,
        environment: Optional[Mapping[str, str]] = None,
        stdin_path: Optional[Path] = None,
        stdout_path: Optional[Path] = None,
        correlation_id: Optional[str] = None,
    ) -> CommandResult:
        if not argv:
            raise InfrastructureError("O argv do comando não pode estar vazio")
        env = os.environ.copy()
        if environment:
            env.update(environment)
        started = time.monotonic()
        stdin_handle = stdin_path.open("rb") if stdin_path else None
        stdout_handle = stdout_path.open("wb") if stdout_path else subprocess.PIPE
        try:
            completed = subprocess.run(  # noqa: S603 - apenas argv; shell desabilitado
                list(argv),
                cwd=str(cwd) if cwd else None,
                env=env,
                stdin=stdin_handle,
                stdout=stdout_handle,
                stderr=subprocess.PIPE,
                timeout=timeout,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandTimeoutError(f"O comando excedeu o limite de {timeout}s") from exc
        except OSError as exc:
            raise InfrastructureError(f"Não foi possível executar o comando: {exc}") from exc
        finally:
            if stdin_handle:
                stdin_handle.close()
            if stdout_path and stdout_handle is not subprocess.PIPE:
                stdout_handle.close()  # type: ignore[union-attr]
        stdout = completed.stdout.decode(errors="replace") if completed.stdout else ""
        stderr = completed.stderr.decode(errors="replace") if completed.stderr else ""
        return CommandResult(
            self._redactor.argv(argv),
            completed.returncode,
            self._redactor.redact(stdout),
            self._redactor.redact(stderr),
            time.monotonic() - started,
            correlation_id,
        )
