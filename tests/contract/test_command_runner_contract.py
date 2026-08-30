from wp_modernizer.infrastructure.command import SubprocessCommandRunner


def test_runner_returns_structured_result_without_shell() -> None:
    result = SubprocessCommandRunner().run(["python3", "-c", "print('ok')"])
    assert result.return_code == 0
    assert result.stdout.strip() == "ok"
    assert result.elapsed_seconds >= 0
