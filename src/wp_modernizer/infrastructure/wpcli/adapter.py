import os
import tempfile
from pathlib import Path
from typing import Mapping, Sequence

from wp_modernizer.application.ports import CommandRunner
from wp_modernizer.domain.errors import WordPressUnavailableError


class WPCLIAdapter:
    def __init__(self, runner: CommandRunner, binary: str = "wp") -> None:
        self._runner = runner
        self._binary = binary

    def get_site_url(self, path: Path, run_id: str) -> str:
        result = self._runner.run(
            [
                self._binary,
                f"--path={path}",
                "--skip-plugins",
                "--skip-themes",
                "option",
                "get",
                "siteurl",
            ],
            timeout=60,
            correlation_id=run_id,
        )
        if result.return_code != 0:
            raise WordPressUnavailableError(result.stderr)
        return result.stdout.strip()

    def get_config(self, path: Path, name: str, run_id: str) -> str:
        result = self._runner.run(
            [self._binary, f"--path={path}", "config", "get", name],
            timeout=60,
            correlation_id=run_id,
        )
        if result.return_code != 0:
            raise WordPressUnavailableError(result.stderr)
        return result.stdout.strip()

    def search_replace(
        self, path: Path, old_url: str, new_url: str, *, dry_run: bool, multisite: bool, run_id: str
    ) -> str:
        argv = [
            self._binary,
            f"--path={path}",
            "--skip-plugins",
            "--skip-themes",
            "search-replace",
            old_url.rstrip("/"),
            new_url.rstrip("/"),
            "--all-tables-with-prefix",
            "--precise",
            "--report-changed-only",
        ]
        if dry_run:
            argv.append("--dry-run")
        if multisite:
            argv.append("--network")
        result = self._runner.run(argv, timeout=600, correlation_id=run_id)
        if result.return_code != 0:
            raise WordPressUnavailableError(
                f"falha no search-replace que considera serialização: {result.stderr}"
            )
        return result.stdout

    def set_config(self, path: Path, values: Mapping[str, str], run_id: str) -> None:
        for name, value in values.items():
            if "\n" in value or "\r" in value:
                raise WordPressUnavailableError(
                    f"o valor de configuração {name} contém quebra de linha insegura"
                )
            stdin_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
                    stdin_path = Path(handle.name)
                    handle.write(value + "\n")
                os.chmod(stdin_path, 0o600)
                result = self._runner.run(
                    [
                        self._binary,
                        f"--path={path}",
                        "--prompt=value",
                        "config",
                        "set",
                        name,
                    ],
                    stdin_path=stdin_path,
                    timeout=60,
                    correlation_id=run_id,
                )
            finally:
                if stdin_path is not None:
                    stdin_path.unlink(missing_ok=True)
            if result.return_code != 0:
                raise WordPressUnavailableError(
                    f"falha ao definir {name} no wp-config; consulte o log redigido"
                )

    def update(self, path: Path, arguments: Sequence[str], run_id: str) -> str:
        result = self._runner.run(
            [self._binary, f"--path={path}", "--skip-plugins", "--skip-themes", *arguments],
            timeout=900,
            correlation_id=run_id,
        )
        if result.return_code != 0:
            raise WordPressUnavailableError(result.stderr)
        return result.stdout
