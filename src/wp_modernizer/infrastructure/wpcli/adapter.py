from pathlib import Path
from typing import Sequence

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

    def is_multisite(self, path: Path, run_id: str) -> bool:
        result = self._runner.run(
            [self._binary, f"--path={path}", "config", "get", "MULTISITE", "--type=constant"],
            timeout=60,
            correlation_id=run_id,
        )
        if result.return_code != 0:
            return False
        return result.stdout.strip().lower() in {"1", "true"}

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
    ) -> int:
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
            "--format=count",
        ]
        if dry_run:
            argv.append("--dry-run")
        if multisite:
            argv.append("--network")
        result = self._runner.run(argv, timeout=600, correlation_id=run_id)
        if result.return_code != 0:
            raise WordPressUnavailableError(
                "falha no search-replace que considera serialização; consulte o log redigido"
            )
        try:
            return int(result.stdout.strip())
        except ValueError as exc:
            raise WordPressUnavailableError(
                "WP-CLI retornou uma contagem inválida para o search-replace"
            ) from exc

    def update(self, path: Path, arguments: Sequence[str], run_id: str) -> str:
        result = self._runner.run(
            [self._binary, f"--path={path}", "--skip-plugins", "--skip-themes", *arguments],
            timeout=900,
            correlation_id=run_id,
        )
        if result.return_code != 0:
            raise WordPressUnavailableError(result.stderr)
        return result.stdout
