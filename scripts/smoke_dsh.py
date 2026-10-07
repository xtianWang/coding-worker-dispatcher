"""Opt-in real DSH check using one disposable, synthetic Git repository."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from coding_worker_dispatcher.config import load_config
from coding_worker_dispatcher.dispatcher import dispatch
from coding_worker_dispatcher.models import Task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-external-run", action="store_true",
        help="Run one DSH model task. This can use credits and save a DSH session.",
    )
    parser.add_argument("--config", help="Optional trusted worker configuration")
    args = parser.parse_args()
    if not args.confirm_external_run:
        parser.error("Pass --confirm-external-run to run the real DSH task.")

    config = load_config(args.config)
    base = ROOT / "local"
    base.mkdir(exist_ok=True)
    cwd = Path(tempfile.mkdtemp(prefix="dsh-smoke-", dir=base)).resolve()
    (cwd / "README.txt").write_bytes(b"DISPATCHER_SMOKE_OK\n")

    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}

    def git(*arguments):
        subprocess.run(
            ["git", "-C", str(cwd), "-c", "core.hooksPath=.git/hooks", *arguments],
            check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10,
        )

    git("init")
    git("config", "core.autocrlf", "false")
    git("add", "README.txt")
    git(
        "-c", "user.name=Dispatcher smoke fixture",
        "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-m", "Synthetic smoke fixture",
    )

    worker = dict(config.get("workers", {}).get("dsh", {}))
    worker.update(enabled=True, allow_write=False, accept_provider_policy=True)
    config.update(
        external_dispatch=True,
        allowed_roots=[str(cwd)],
        max_timeout_seconds=60,
        max_output_bytes=1024 * 1024,
        workers={"dsh": worker},
    )
    task = Task(
        "dsh",
        "Read README.txt and reply with its only line. Do not edit files, run shell "
        "commands, start subagents, or use browser/network tools. This is a read-only "
        "connectivity smoke test.",
        str(cwd), timeout_seconds=60, task_type="smoke_read_only",
    )
    result = dispatch(task, config)
    passed = (
        result["status"] == "completed"
        and result["summary"].strip() == "DISPATCHER_SMOKE_OK"
        and not result["files_changed"]
        and not result.get("git", {}).get("violations")
    )
    print(json.dumps({"fixture": str(cwd), "smoke_passed": passed, "result": result}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
