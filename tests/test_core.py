import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from coding_worker_dispatcher.cli import main
from coding_worker_dispatcher.config import load_config
from coding_worker_dispatcher.dispatcher import dispatch
from coding_worker_dispatcher.git_checks import workspace_lock
from coding_worker_dispatcher.models import DispatchError, Task
from coding_worker_dispatcher.runner import run
from coding_worker_dispatcher.safety import allowed_targets, redact, resolve_command, environment
from coding_worker_dispatcher.discovery import connection
from coding_worker_dispatcher.workers.dsh import Dsh
from coding_worker_dispatcher.workers.zcode import Zcode

FAKE = ROOT / "tests" / "fixtures" / "fake_worker.py"


class Contracts(unittest.TestCase):
    def test_invalid_tasks(self):
        for override in ({"worker": "unknown"}, {"prompt": ""}, {"cwd": "relative"},
                         {"timeout_seconds": float("nan")}, {"timeout_seconds": True},
                         {"mode": "write"}, {"mode": "read_only", "allowed_paths": ["x"]}):
            with self.subTest(override=override), self.assertRaises(DispatchError):
                Task.from_dict({"worker": "dsh", "prompt": "inspect", "cwd": str(ROOT), **override})

    def test_default_external_disabled(self):
        result = dispatch(Task("dsh", "inspect", str(ROOT)), {})
        self.assertEqual(result["error"]["code"], "external_dispatch_disabled")
        self.assertEqual(result["attempts"], 0)

    def test_codex_handoff(self):
        result = dispatch(Task("codex", "inspect", str(ROOT)), {})
        self.assertEqual(result["status"], "delegated_back")
        self.assertEqual(result["attempts"], 0)

    def test_redaction(self):
        text = "Authorization: Bearer abc\nCookie: session=abc\napi_key=abcdef\neyJabc.eyJdef.abcdef\nsk-1234567890"
        masked = redact(text)
        for value in ("Bearer abc", "session=abc", "abcdef", "eyJabc", "sk-123"):
            self.assertNotIn(value, masked)
        self.assertNotIn("custom123", redact("custom123", ["custom123"]))

    def test_dsh_protocol(self):
        complete = '{"type":"status","phase":"turn_end","reason":{"kind":"completed"}}\n'
        self.assertEqual(Dsh().parse(complete + '{"type":"final","text":"ok"}'), "ok")
        for value in ('garbage', '{}', '[]', '{"type":"final","text":"ok"}',
                      '{"type":"error"}\n' + complete + '{"type":"final","text":"ok"}',
                      '{"type":"status","phase":"turn_end","reason":{"kind":"error"}}\n'
                      '{"type":"final","text":""}'):
            with self.subTest(value=value), self.assertRaises(DispatchError):
                Dsh().parse(value)

    def test_zcode_protocol(self):
        self.assertEqual(Zcode().parse('{"response":"ok"}'), "ok")
        with self.assertRaises(DispatchError):
            Zcode().parse('{"unknown":"ok"}')

    def test_zcode_model_selection_failure(self):
        error = Zcode().classify_failure(
            "Error: Model creation failed\nCause: Error: Select a model before continuing")
        self.assertEqual(error.code, "model_selection_unavailable")
        self.assertEqual(Zcode().classify_failure("Error: Model creation failed").code,
                         "model_creation_failed")
        self.assertIsNone(Zcode().classify_failure("generic worker error"))

    def test_zcode_readonly_rejected(self):
        with self.assertRaises(DispatchError) as error:
            Zcode().invocation(Task("zcode", "inspect", str(ROOT)), {}, ["runtime"], {})
        self.assertEqual(error.exception.code, "permission_unsupported")

    def test_zcode_never_implicit_yolo(self):
        task = Task("zcode", "fix", str(ROOT), mode="write", allowed_paths=["a"])
        args = Zcode().invocation(task, {}, ["runtime", "cli"], {}).command
        self.assertEqual(args[args.index("--mode") + 1], "edit")
        self.assertNotIn("yolo", args)
        self.assertIn("--json", args)
        self.assertNotIn("--output-format", args)
        self.assertNotIn("--verbose", args)
        verbose_args = Zcode().invocation(
            task, {"verbose": True}, ["runtime", "cli"], {}).command
        self.assertIn("--verbose", verbose_args)

    def test_zcode_configurable_builtin_provider_file(self):
        task = Task("zcode", "inspect", str(ROOT), mode="write", allowed_paths=["a"])
        with tempfile.TemporaryDirectory() as directory:
            provider = Path(directory) / "provider.json"
            provider.write_text("{}", encoding="utf-8")
            original_env = {"PATH": "safe"}
            invocation = Zcode().invocation(
                task, {"builtin_provider_config_file": str(provider)}, ["runtime"], original_env)
        self.assertEqual(invocation.env["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"], str(provider))
        self.assertEqual(original_env, {"PATH": "safe"})
        with self.assertRaises(DispatchError):
            Zcode().invocation(
                task, {"builtin_provider_config_file": "missing.json"}, ["runtime"], {})

    def test_zcode_discovers_moved_builtin_provider_file(self):
        task = Task("zcode", "inspect", str(ROOT), mode="write", allowed_paths=["a"])
        with tempfile.TemporaryDirectory() as directory:
            resources = Path(directory)
            entry = resources / "glm" / "zcode.cjs"
            entry.parent.mkdir()
            entry.write_text("", encoding="utf-8")
            provider = resources / "config" / "provider" / "zcode-builtin.json"
            provider.parent.mkdir(parents=True)
            provider.write_text("{}", encoding="utf-8")
            invocation = Zcode().invocation(task, {}, ["node", str(entry)], {})
            self.assertEqual(invocation.env["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"],
                             str(provider))

    def test_dsh_env_scoped(self):
        env = {"ORIGINAL": "yes"}
        inv = Dsh().invocation(Task("dsh", "inspect", str(ROOT)),
                               {"electron": True, "patch": "user-overlay.yml"}, ["runtime"], env)
        self.assertEqual(inv.env["DSH_PERMISSION_MODE"], "read-only")
        self.assertNotIn("DSH_PERMISSION_MODE", env)
        self.assertEqual(inv.command[-2:], ["--json", "-"])
        self.assertEqual(inv.command.count("--patch"), 2)
        safety_overlay = Path(inv.command[-3])
        self.assertEqual(inv.command[-4], "--patch")
        self.assertEqual(safety_overlay.name, "dsh-no-subagents.yml")
        overlay_text = safety_overlay.read_text(encoding="utf-8")
        for tool_id in ("tool-subagent", "tool-subagent-fork", "tool-subagent-control",
                        "tool-subagent-list-agents"):
            self.assertIn(f"id: {tool_id}\n  disabled: true", overlay_text)

    def test_paths_and_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for value in ("../escape", ".git/config", "C:escape"):
                with self.subTest(value=value), self.assertRaises(DispatchError):
                    allowed_targets(root, [value])
            wrapper = root / "worker.cmd"
            wrapper.write_text("@echo off")
            with self.assertRaises(DispatchError):
                resolve_command([str(wrapper)], os.environ)
        with self.assertRaises(DispatchError):
            resolve_command(["clearly-missing-worker-93829"], {"PATH": ""})

    def test_config_types(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            for content in ('external_dispatch = "false"', 'allowed_roots = ["relative"]',
                            'max_output_bytes = 0', '[workers.dsh]\nenabled = "yes"',
                            '[workers.zcode]\nbuiltin_provider_config_file = "relative.json"'):
                path.write_text(content)
                with self.subTest(content=content), self.assertRaises(DispatchError):
                    load_config(path)

    def test_cli_json_and_no_launch(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["run", "--worker", "codex", "--cwd", str(ROOT), "--prompt", "inspect", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "delegated_back")


    def test_environment_names_case_insensitive(self):
        with patch.dict(os.environ, {"SYSTEMROOT": "system", "Path": "bin", "UNRELATED_SECRET": "hidden"}, clear=True):
            env, secrets = environment({})
        self.assertEqual(env["SYSTEMROOT"], "system")
        self.assertEqual(next(v for k, v in env.items() if k.upper() == "PATH"), "bin")
        self.assertNotIn("UNRELATED_SECRET", env)

    def test_configured_command_wins(self):
        settings = connection("dsh", {"command": ["explicit-runtime", "entry"]})
        self.assertEqual(settings["command"], ["explicit-runtime", "entry"])
        self.assertEqual(settings["connection_source"], "configured")

    def test_doctor_extracts_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text("[workers.dsh]\ncommand = " + json.dumps([sys.executable, str(FAKE)]))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["--config", str(path), "doctor", "--worker", "dsh", "--probe"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["version"], "0.2.0-rc.2")


class RunnerTests(unittest.TestCase):
    def execute(self, mode, **kwargs):
        return run([sys.executable, "-X", "utf8", str(FAKE), mode], cwd=str(ROOT),
                   env=dict(os.environ), timeout=kwargs.pop("timeout", 3), **kwargs)

    def test_stdin(self):
        result = self.execute("stdin", stdin="hello 世界\n")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.stdout.replace("\r\n", "\n"), "hello 世界\n\n")
        self.assertTrue(result.cleanup_confirmed)

    def test_nonzero(self):
        result = self.execute("nonzero")
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(result.status, "failed")

    def test_timeout(self):
        result = self.execute("timeout", timeout=0.2)
        self.assertEqual(result.status, "timed_out")
        self.assertTrue(result.cleanup_confirmed)

    def test_both_pipes_bounded(self):
        result = self.execute("flood", max_bytes=8192)
        self.assertEqual(result.status, "output_limit")
        self.assertLessEqual(len(result.stdout) + len(result.stderr), 8192)
        self.assertTrue(result.cleanup_confirmed)

    def test_spawn_failure(self):
        with self.assertRaises(DispatchError):
            run(["nonexistent-executable-92837"], cwd=str(ROOT), env=dict(os.environ))

    def test_descendant_terminated(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / "child.pid"
            result = run([sys.executable, str(FAKE), "child", str(pidfile)],
                         cwd=directory, env=dict(os.environ), timeout=1)
            self.assertEqual(result.status, "timed_out")
            self.assertTrue(result.cleanup_confirmed)
            pid = int(pidfile.read_text())
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes
                api = ctypes.WinDLL("kernel32", use_last_error=True)
                api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                api.OpenProcess.restype = wintypes.HANDLE
                api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                api.CloseHandle.argtypes = [wintypes.HANDLE]
                handle = api.OpenProcess(0x100000, False, pid)
                if handle:
                    try:
                        self.assertEqual(api.WaitForSingleObject(handle, 2000), 0)
                    finally:
                        api.CloseHandle(handle)
            else:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    # A killed child can briefly remain a zombie until init reaps it.
                    stat = Path(f"/proc/{pid}/stat")
                    self.assertTrue(stat.exists() and stat.read_text().split()[2] == "Z")


class GitIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        def git(*args):
            subprocess.run(["git", "-C", str(self.root), *args], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.git = git
        git("init")
        git("config", "core.autocrlf", "false")
        git("config", "user.name", "Fixture")
        git("config", "user.email", "fixture@example.invalid")
        (self.root / "a.txt").write_bytes(b"baseline\n")
        git("add", "a.txt")
        git("-c", "core.hooksPath=/dev/null", "commit", "-m", "fixture baseline")

    def tearDown(self):
        self.temp.cleanup()

    def config(self, mode="ok"):
        return {"external_dispatch": True, "allowed_roots": [str(self.root)],
                "workers": {"dsh": {"enabled": True, "allow_write": True,
                                    "accept_provider_policy": True,
                                    "command": [sys.executable, str(FAKE), mode]}}}

    def task(self, mode="read_only"):
        return Task("dsh", "inspect", str(self.root), mode=mode,
                    allowed_paths=["a.txt"] if mode == "write" else [])

    def test_completed(self):
        result = dispatch(self.task(), self.config())
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["summary"], "Inspected successfully.")
        self.assertEqual(result["acceptance"], "unknown")

    def test_write_allowed(self):
        result = dispatch(self.task("write"), self.config("write"))
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["files_changed"], ["a.txt"])

    def test_dirty_rejected_without_launch(self):
        (self.root / "a.txt").write_text("user edit")
        result = dispatch(self.task("write"), self.config("write"))
        self.assertEqual(result["error"]["code"], "dirty_workspace", result)
        self.assertEqual(result["attempts"], 0)
        self.assertEqual((self.root / "a.txt").read_text(), "user edit")

    def test_untracked_rejected(self):
        (self.root / "user.txt").write_text("user data")
        result = dispatch(self.task("write"), self.config())
        self.assertEqual(result["error"]["code"], "dirty_workspace")

    def test_readonly_violation(self):
        result = dispatch(self.task(), self.config("write"))
        self.assertEqual(result["error"]["code"], "postflight_violation", result)
        self.assertIn("read_only_violation", result["git"]["violations"])

    def test_dirty_readonly_detects_content_change(self):
        (self.root / "a.txt").write_text("user edit")
        result = dispatch(self.task(), self.config("write"))
        self.assertEqual(result["files_changed"], ["a.txt"])
        self.assertEqual(result["status"], "failed")

    def test_outside_allowed_paths(self):
        result = dispatch(self.task("write"), self.config("outside"))
        self.assertIn("outside_allowed_paths", result["git"]["violations"], result)

    def test_nonzero_preserves_partial_changes(self):
        result = dispatch(self.task("write"), self.config("write_fail"))
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["files_changed"], ["a.txt"])
        self.assertEqual((self.root / "a.txt").read_text(), "partial change\n")

    def test_protocol_error_still_checks_git(self):
        result = dispatch(self.task(), self.config("broken"))
        self.assertEqual(result["error"]["code"], "malformed_output", result)
        self.assertTrue(result["git"]["checked"])
        self.assertIn("not JSON", result["output_excerpt"]["stdout"])

    def test_zcode_model_selection_failure_is_actionable(self):
        config = {
            "external_dispatch": True,
            "allowed_roots": [str(self.root)],
            "workers": {"zcode": {
                "enabled": True,
                "allow_write": True,
                "accept_provider_policy": True,
                "command": [sys.executable, str(FAKE), "model_missing"],
            }},
        }
        task = Task("zcode", "inspect", str(self.root), mode="write",
                    allowed_paths=["smoke-output.txt"])
        result = dispatch(task, config)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "model_selection_unavailable", result)
        self.assertTrue(result["git"]["checked"])

    def test_secret_in_summary(self):
        result = dispatch(self.task(), self.config("secret"))
        self.assertNotIn("synthetic-secret-value", json.dumps(result))

    def test_repository_lock(self):
        with workspace_lock(self.root):
            result = dispatch(self.task(), self.config())
        self.assertEqual(result["error"]["code"], "workspace_busy")

    def test_invalid_cwd(self):
        task = self.task()
        task.cwd = str(self.root / "missing")
        result = dispatch(task, self.config())
        self.assertEqual(result["error"]["code"], "invalid_cwd")


if __name__ == "__main__":
    unittest.main()
