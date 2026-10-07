import argparse
import json
import sys
from .config import load_config
from .dispatcher import dispatch
from .discovery import connection
import re
from .models import DispatchError, Task
from .runner import run
from .safety import environment, resolve_command
from .workers import ADAPTERS

EXIT_CODES = {"completed": 0, "delegated_back": 0, "rejected": 2,
              "failed": 3, "timed_out": 4, "cancelled": 130}


def parser():
    p = argparse.ArgumentParser(prog="cwdispatch")
    p.add_argument("--config", help="Explicit trusted user TOML configuration")
    sub = p.add_subparsers(dest="action", required=True)
    workers = sub.add_parser("workers")
    workers.add_argument("--json", action="store_true")
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--worker", choices=ADAPTERS, required=True)
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--probe", action="store_true", help="Execute configured binary with --version (no task)")
    task = sub.add_parser("run")
    task.add_argument("--json", action="store_true")
    task.add_argument("--task-stdin", action="store_true")
    task.add_argument("--worker", choices=("codex", "dsh", "zcode"))
    task.add_argument("--cwd")
    prompt = task.add_mutually_exclusive_group()
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-stdin", action="store_true")
    task.add_argument("--mode", choices=("read_only", "write"), default=None)
    task.add_argument("--timeout", type=float, default=None)
    task.add_argument("--allow-path", action="append", default=[])
    return p


def bounded_stdin():
    data = sys.stdin.buffer.read(131073)
    if len(data) > 131072:
        raise DispatchError("invalid_task", "Input exceeds 128 KiB.")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise DispatchError("invalid_task", "stdin must be UTF-8.") from None


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if args.action == "workers":
            data = {"external_dispatch": config.get("external_dispatch", False),
                    "workers": [{"name": "codex", "kind": "handoff"},
                                *[{"name": name, "kind": "external", "experimental": True,
                                   "enabled": config.get("workers", {}).get(name, {}).get("enabled", False)}
                                  for name in ADAPTERS]]}
        elif args.action == "doctor":
            settings = connection(args.worker, config.get("workers", {}).get(args.worker, {}))
            env, _ = environment(settings)
            command = resolve_command(settings.get("command", []), env)
            data = {"worker": args.worker, "executable_found": True, "authentication": "unknown",
                    "connection_source": settings.get("connection_source", "unknown"),
                    "runtime_checked": False, "permission_assurance": "unverified"}
            if args.probe:
                if args.worker == "dsh" and settings.get("electron"):
                    env["ELECTRON_RUN_AS_NODE"] = "1"
                execution = run([*command, "--version"], cwd=None, env=env, timeout=10, max_bytes=16384)
                data.update(runtime_checked=True, status=execution.status, exit_code=execution.exit_code)
                version = re.search(r"(?<!\w)\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?", execution.stdout)
                data["version"] = version.group() if version else None
                if execution.status != "completed":
                    raise DispatchError("probe_failed", "Worker version probe failed.")
        else:
            if args.task_stdin:
                if any((args.worker, args.cwd, args.prompt, args.prompt_stdin, args.mode, args.timeout, args.allow_path)):
                    raise DispatchError("invalid_task", "--task-stdin cannot be mixed with task fields.")
                try:
                    task = Task.from_dict(json.loads(bounded_stdin()))
                except (ValueError, RecursionError):
                    raise DispatchError("invalid_task", "Invalid Task JSON.") from None
            else:
                prompt = bounded_stdin() if args.prompt_stdin else args.prompt
                task = Task.from_dict({"worker": args.worker, "cwd": args.cwd, "prompt": prompt,
                                       "mode": args.mode or "read_only",
                                       "timeout_seconds": args.timeout if args.timeout is not None else 300,
                                       "allowed_paths": args.allow_path})
            data = dispatch(task, config)
        print(json.dumps(data, ensure_ascii=True, allow_nan=False))
        return EXIT_CODES.get(data.get("status"), 0)
    except DispatchError as exc:
        print(json.dumps({"schema_version": 1, "status": "rejected",
                          "error": {"code": exc.code, "message": str(exc), "retryable": False}}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
