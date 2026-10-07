import os
from pathlib import Path
import re
import shutil
from .models import DispatchError

# Best effort output masking, never an egress boundary.
PATTERNS = [
    (re.compile(r"(?im)(authorization\s*[:=]\s*)[^\r\n]+"), r"\1[REDACTED]"),
    (re.compile(r"(?im)((?:set-cookie|cookie)\s*[:=]\s*)[^\r\n]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[^\s\"'<>]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,}]+"), r"\1[REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), "[REDACTED]"),
    (re.compile(r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{8,}\b"), "[REDACTED]"),
]
ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def redact(text, secrets=()):
    text = ANSI.sub("", text)
    for value in sorted(set(secrets), key=len, reverse=True):
        if value:
            text = text.replace(value, "[REDACTED]")
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def sanitize(value, secrets=()):
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, list):
        return [sanitize(x, secrets) for x in value]
    if isinstance(value, dict):
        return {redact(str(k), secrets): sanitize(v, secrets) for k, v in value.items()}
    return value


def inside(path, root):
    return path == root or root in path.parents


def linked(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def check_cwd(cwd, roots):
    path = Path(cwd)
    if not path.is_dir():
        raise DispatchError("invalid_cwd", "cwd does not exist or is not a directory.")
    resolved = path.resolve()
    if not any(inside(resolved, Path(r).resolve()) for r in roots):
        raise DispatchError("unsafe_request", "cwd is outside configured allowed_roots.")
    for parent in (path, *path.parents):
        if linked(parent):
            raise DispatchError("unsafe_request", "Linked cwd ancestors are unsupported.")
    return resolved


def allowed_targets(root, paths):
    targets = []
    for value in paths:
        relative = Path(value)
        if relative.is_absolute() or relative.drive or ".." in relative.parts or ":" in value:
            raise DispatchError("unsafe_request", "allowed_paths must stay inside the repository.")
        target = root / relative
        resolved = target.resolve()
        if not inside(resolved, root) or any(p.lower() == ".git" for p in relative.parts):
            raise DispatchError("unsafe_request", "Git metadata and outside paths cannot be granted.")
        for parent in (target, *target.parents):
            if parent == root:
                break
            if linked(parent):
                raise DispatchError("unsafe_request", "Linked allowed_paths are unsupported.")
        targets.append(resolved)
    return targets


def environment(settings):
    base = ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE",
            "APPDATA", "LOCALAPPDATA", "LANG", "LC_ALL")
    names = {k.upper() for k in (*base, *settings.get("env_allowlist", []))}
    env = {k: v for k, v in os.environ.items() if k.upper() in names}
    # Explicitly allowing these still cannot change runner semantics.
    for key in list(env):
        if key.upper() in ("NODE_OPTIONS", "PYTHONPATH", "PYTHONSTARTUP", "ELECTRON_RUN_AS_NODE"):
            env.pop(key)
    secrets = [v for k, v in env.items()
               if re.search(r"key|token|secret|password|cookie|authorization", k, re.I)]
    return env, secrets


def resolve_command(command, env):
    if not command:
        raise DispatchError("worker_unavailable", "No worker command is configured.")
    exe = command[0]
    resolved = str(Path(exe).resolve()) if Path(exe).is_absolute() else shutil.which(exe, path=env.get("PATH", ""))
    if not resolved or not Path(resolved).is_file():
        raise DispatchError("worker_unavailable", "Configured executable was not found.")
    if Path(resolved).suffix.lower() in (".cmd", ".bat", ".ps1"):
        raise DispatchError("unsupported_launcher", "Configure the native runtime and entrypoint, not a shell wrapper.")
    return [resolved, *command[1:]]
