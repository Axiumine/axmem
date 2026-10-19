"""Harness entrypoint: `uv run python -m test_harness [flags]`.

Exit codes: 0 = every session completed (issues found are the product's problem, not the
harness's); 1 = one or more sessions did not complete (timeout/error) so the UAT is
partial; 2 = harness infrastructure failure (docker/image/container problem, server
boot failed, agent unhealthy, throwaway server unreachable from the container) or
invalid usage/configuration (nothing was started); 143 = terminated by SIGTERM (after the
same teardown as Ctrl-C).
"""
import argparse
import asyncio
import signal
import sys
import time
from contextlib import ExitStack
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import SettingsError

from test_harness.config import HarnessConfig
from test_harness.container import HOST_ALIAS, AgentContainer, docker_sdk, ensure_image
from test_harness.server import HarnessInfraError, ThrowawayForgetful
from test_harness.walkthrough import (
    FIXTURE_SKILL,
    SkillRunResult,
    Walkthrough,
    preflight_fixture_repo,
)


def config_from_argv(argv: list[str]) -> HarnessConfig:
    parser = argparse.ArgumentParser(
        prog="test_harness",
        description="Walk a real agent through the canonical skills against a "
        "throwaway local Forgetful, inside a sandbox container.",
    )
    parser.add_argument("--agent", help="agent-shell agent type (default: opencode)")
    parser.add_argument("--model", help="model to pin (default: opencode-go/deepseek-v4-flash)")
    parser.add_argument("--effort", help="reasoning effort passthrough")
    parser.add_argument("--surface", help="surface to walk (v1: cli)")
    parser.add_argument("--skills", help="comma-separated subset of the walkthrough skills")
    parser.add_argument("--timeout", type=float, help="per-skill timeout in seconds")
    parser.add_argument("--output", type=Path, help="artifacts root (default: test_harness/runs)")
    parser.add_argument(
        "--rebuild-image", action="store_true",
        help="force a rebuild of the sandbox image without the layer cache, e.g. to pick "
        "up a new opencode release (it also rebuilds itself when locked dependencies "
        "change)",
    )
    args = parser.parse_args(argv)

    overrides = {
        "agent_type": args.agent,
        "model": args.model,
        "effort": args.effort,
        "surface": args.surface,
        "skills": args.skills,  # raw text: HarnessConfig splits and validates it
        "skill_timeout": args.timeout,
        "output_dir": args.output,
        "rebuild_image": args.rebuild_image or None,
    }
    return HarnessConfig(**{name: value for name, value in overrides.items() if value is not None})


def _print_summary(results: list[SkillRunResult], run_dir: Path) -> None:
    print(f"\n{'skill':<28} {'status':<10} {'report':<12} {'verdict':<8} issues breaches")
    for result in results:
        report = result.report_load
        verdict = report.report.verdict if report.report else "-"
        issues = len(report.report.issues) if report.report else "-"
        print(
            f"{result.skill:<28} {result.status:<10} {report.status:<12} "
            f"{verdict:<8} {issues!s:<6} {len(result.breaches)}",
        )
    print(f"\nartifacts: {run_dir}")


def _cancel_on_sigterm(teardown: ExitStack) -> None:
    """Make SIGTERM (`kill <pid>`, a CI cancel, `timeout`) cancel the run like Ctrl-C.

    Python's default SIGTERM action ends the process without unwinding, so none of the
    teardown would run and the container, the server and the credential copies would
    outlive the harness. Like the first Ctrl-C, the cancellation lands at the next
    `await`, then flows through the teardown stack.
    """
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    try:
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
    except (NotImplementedError, RuntimeError):
        return  # Windows event loops and non-main threads cannot take signal handlers
    teardown.callback(loop.remove_signal_handler, signal.SIGTERM)


async def _run(config: HarnessConfig) -> int:
    # Fail before any work: the container step would only hit a missing SDK after the
    # image build and the server boot.
    docker_sdk()
    if FIXTURE_SKILL in config.skills:
        preflight_fixture_repo()
    run_dir = config.output_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{config.agent_type}"
    run_dir.mkdir(parents=True, exist_ok=False)

    ensure_image(rebuild=config.rebuild_image)

    server = ThrowawayForgetful(run_dir=run_dir)
    # Teardown callbacks run last-in first-out and every one runs even when an earlier
    # one raises, so a docker failure can never orphan the server process.
    with ExitStack() as teardown:
        _cancel_on_sigterm(teardown)
        teardown.callback(server.stop)
        print("booting throwaway Forgetful ...")
        server.start()
        # From inside the container 127.0.0.1 is the container itself; Docker Desktop
        # routes host.docker.internal back to the WSL host (verified empirically).
        container = AgentContainer(
            config, run_dir,
            server_url=server.url.replace("127.0.0.1", HOST_ALIAS),
        )
        teardown.callback(container.stop)

        print("starting sandbox container (installs the working tree) ...")
        await asyncio.to_thread(container.start)
        print(f"preflight: {container.server_url} reachable from inside the container ...")
        await asyncio.to_thread(container.check_server_reachable)
        print(f"preflight: in-container health check for {config.agent_type} / {config.model} ...")
        await asyncio.to_thread(container.health_check)

        walkthrough = Walkthrough(
            config=config, runner=container, server_url=server.url, run_dir=run_dir,
        )
        results = []
        for skill in config.skills:
            print(f"walking {skill} ...")
            results.append(await walkthrough.run_skill(skill))
        walkthrough.write_summary(results)

    _print_summary(results, run_dir)
    return 0 if all(result.status == "completed" for result in results) else 1


def _usage_error_text(exc: ValidationError | SettingsError) -> str:
    if isinstance(exc, SettingsError):
        return str(exc)
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: "
        f"{error['msg'].removeprefix('Value error, ')}"
        for error in exc.errors()
    )


def main(argv: list[str] | None = None) -> int:
    try:
        config = config_from_argv(sys.argv[1:] if argv is None else argv)
    except (ValidationError, SettingsError) as exc:
        print(f"usage error: {_usage_error_text(exc)}", file=sys.stderr)
        return 2
    try:
        return asyncio.run(_run(config))
    except HarnessInfraError as exc:
        print(f"infra error: {exc}", file=sys.stderr)
        return 2
    except asyncio.CancelledError:
        # only the SIGTERM handler cancels the main task (Ctrl-C surfaces as
        # KeyboardInterrupt); the teardown has already run
        print("terminated by SIGTERM", file=sys.stderr)
        return 128 + signal.SIGTERM


if __name__ == "__main__":
    sys.exit(main())
