import json
from pathlib import Path
from .base import Invocation
from ..models import DispatchError


class Dsh:
    name = "dsh"

    def invocation(self, task, settings, command, env):
        env = dict(env)
        if settings.get("electron", False):
            env["ELECTRON_RUN_AS_NODE"] = "1"
        env["DSH_PERMISSION_MODE"] = "read-only" if task.mode == "read_only" else "workspace-write"
        env["DSH_TELEMETRY_DISABLED"] = "1"
        args = [*command, "--profile", settings.get("profile", "headless")]
        if settings.get("patch"):
            args += ["--patch", settings["patch"]]
        # Apply our fixed overlay last so a user overlay cannot re-enable the
        # built-in subagent tools for a single-task dispatcher invocation.
        safety_patch = Path(__file__).with_name("dsh-no-subagents.yml")
        args += ["--patch", str(safety_patch)]
        return Invocation([*args, "--json", "-"], task.prompt, env)

    def parse(self, stdout):
        final = None
        failed = False
        completed = False
        try:
            for line in stdout.splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError()
                if event.get("type") == "error":
                    failed = True
                if event.get("type") == "status" and event.get("phase") == "turn_end":
                    reason = event.get("reason")
                    kind = reason.get("kind") if isinstance(reason, dict) else reason
                    if kind == "completed":
                        completed = True
                    else:
                        failed = True
                if event.get("type") == "final":
                    final = event.get("text")
            if failed or not completed:
                raise DispatchError("worker_reported_failure", "DSH reported a failed run.")
            if not isinstance(final, str):
                raise ValueError()
            return final
        except (ValueError, RecursionError):
            raise DispatchError("malformed_output", "DSH did not return a valid final event.") from None
