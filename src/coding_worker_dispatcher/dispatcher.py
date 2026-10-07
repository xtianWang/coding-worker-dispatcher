import time
from .config import DEFAULT_LIMIT
from .discovery import connection
from .git_checks import compare, git, repository, snapshot, workspace_lock
from .models import DispatchError, Result
from .runner import run
from .safety import allowed_targets, check_cwd, environment, redact, resolve_command, sanitize
from .workers import ADAPTERS


def dispatch(task, config):
    started = time.monotonic()
    result = Result(worker=task.worker, task_id=task.task_id)
    secrets = []
    try:
        task.validate()
        if task.worker == "codex":
            result.status = "delegated_back"
            result.summary = "Keep this task with the primary agent; no child was started."
            return result.to_dict()
        if not config.get("external_dispatch", False):
            raise DispatchError("external_dispatch_disabled", "External dispatch is disabled.")
        settings = connection(task.worker, config.get("workers", {}).get(task.worker, {}))
        if not settings.get("enabled", False):
            raise DispatchError("worker_disabled", "Selected worker is disabled.")
        if not settings.get("accept_provider_policy", False):
            raise DispatchError("policy_not_accepted",
                                "This experimental adapter relies on worker policy, not a dispatcher sandbox.")
        if task.mode == "write" and not settings.get("allow_write", False):
            raise DispatchError("write_disabled", "Worker write mode is disabled.")
        if task.timeout_seconds > config.get("max_timeout_seconds", 300):
            raise DispatchError("invalid_task", "Task timeout exceeds configured maximum.")
        env, secrets = environment(settings)
        command = resolve_command(settings.get("command", []), env)
        adapter = ADAPTERS[task.worker]
        invocation = adapter.invocation(task, settings, command, env)
        cwd = check_cwd(task.cwd, config.get("allowed_roots", []))
        root, metadata = repository(cwd)
        targets = allowed_targets(root, task.allowed_paths)
        for target in targets:
            _, ignored = git(root, "check-ignore", "-q", "--", str(target), allow_failure=True)
            if ignored == 0:
                raise DispatchError("unsafe_request", "Ignored paths cannot be explicit write targets.")
        result.safety = {
            "assurance": "provider_requested_unverified",
            "mode_requested": task.mode,
            "allowed_paths_enforcement": "postflight_only",
            "network_restricted": False,
            "worker_may_persist_sessions": True,
            "prompt_transport": "stdin" if task.worker == "dsh" else "argv",
            "limitations": ["No guarantee against worker commit/push or external side effects.",
                           "Other editors must not write to this repository during execution.",
                           "Worker configuration may load plugins, hooks and credentials.",
                           "Custom DSH plugins may add agent-like tools outside the built-in subagent entries."],
        }
        with workspace_lock(root):
            before = snapshot(root, metadata)
            if task.mode == "write" and before["status"]:
                raise DispatchError("dirty_workspace", "write requires a clean Git workspace including untracked files.")
            try:
                result.attempts = 1
                execution = run(invocation.command, cwd=cwd, env=invocation.env,
                                stdin=invocation.stdin, timeout=task.timeout_seconds,
                                max_bytes=config.get("max_output_bytes", DEFAULT_LIMIT))
                result.exit_code = execution.exit_code
                result.truncated = execution.truncated
                result.safety["cleanup_confirmed"] = execution.cleanup_confirmed
                result.output_excerpt = {
                    "stderr": redact(execution.stderr, secrets)[:4096],
                }
                result.truncated |= len(redact(execution.stderr, secrets)) > 4096
                if execution.status == "completed":
                    try:
                        parsed_summary = adapter.parse(execution.stdout)
                    except DispatchError:
                        excerpt = redact(execution.stdout, secrets)
                        result.output_excerpt["stdout"] = excerpt[:4096]
                        result.truncated |= len(excerpt) > 4096
                        raise
                    summary = redact(parsed_summary, secrets)
                    result.summary = summary[:2000]
                    result.truncated |= len(summary) > 2000
                    result.summary_source = "worker"
                    result.status = "completed"
                else:
                    result.status = execution.status if execution.status in ("timed_out", "cancelled") else "failed"
                    classified = None
                    if execution.status == "failed":
                        classify_failure = getattr(adapter, "classify_failure", None)
                        if callable(classify_failure):
                            classified = classify_failure(execution.stderr)
                    result.error = {
                        "code": classified.code if classified else execution.status,
                        "message": str(classified) if classified else "Worker did not complete normally.",
                        "retryable": False,
                    }
            finally:
                try:
                    after = snapshot(root, metadata)
                    result.files_changed, result.git = compare(root, before, after, task.mode, targets)
                    if result.git["violations"]:
                        result.status = "failed"
                        result.error = {"code": "postflight_violation",
                                        "message": "Workspace checks found changes requiring review; no rollback performed.",
                                        "retryable": False}
                except (DispatchError, OSError):
                    result.status = "failed"
                    result.git = {"checked": False}
                    result.error = {"code": "postflight_failed",
                                    "message": "Workspace state could not be verified; inspect it manually.",
                                    "retryable": False}
    except DispatchError as exc:
        if not result.error:
            result.status = "failed" if result.attempts else "rejected"
            result.error = {"code": exc.code, "message": str(exc), "retryable": False}
    except OSError:
        result.status = "failed" if result.attempts else "rejected"
        result.error = {"code": "io_error", "message": "A filesystem operation failed.", "retryable": False}
    finally:
        result.duration_ms = int((time.monotonic() - started) * 1000)
    return sanitize(result.to_dict(), secrets)
