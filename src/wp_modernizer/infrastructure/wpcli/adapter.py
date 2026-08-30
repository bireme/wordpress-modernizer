from pathlib import Path
from typing import Sequence

from wp_modernizer.application.ports import CommandRunner
from wp_modernizer.domain.errors import WordPressUnavailableError


class WPCLIAdapter:
    def __init__(self, runner: CommandRunner, binary: str = "wp") -> None:
        self._runner = runner
        self._binary = binary

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

    def update(self, path: Path, arguments: Sequence[str], run_id: str) -> str:
        result = self._runner.run(
            [self._binary, f"--path={path}", "--skip-plugins", "--skip-themes", *arguments],
            timeout=900,
            correlation_id=run_id,
        )
        if result.return_code != 0:
            raise WordPressUnavailableError(result.stderr)
        return result.stdout
