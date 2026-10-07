import os
from pathlib import Path
import tomllib
from .models import DispatchError

DEFAULT_LIMIT = 1024 * 1024


def default_path():
    base = Path(os.environ.get("APPDATA", Path.home() / ".config"))
    return base / "coding-worker-dispatcher" / "config.toml"


def load_config(path=None):
    path = Path(path) if path else default_path()
    if not path.exists():
        if path != default_path():
            raise DispatchError("invalid_config", "Explicit config file does not exist.")
        return {"external_dispatch": False, "allowed_roots": [], "workers": {}}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise DispatchError("invalid_config", "Cannot read valid TOML configuration.") from None
    if not isinstance(data.get("external_dispatch", False), bool):
        raise DispatchError("invalid_config", "external_dispatch must be boolean.")
    roots = data.get("allowed_roots", [])
    if not isinstance(roots, list) or not all(
            isinstance(p, str) and Path(p).is_absolute() for p in roots):
        raise DispatchError("invalid_config", "allowed_roots must contain absolute paths.")
    workers = data.get("workers", {})
    if not isinstance(workers, dict) or any(k not in ("dsh", "zcode") for k in workers):
        raise DispatchError("invalid_config", "Only dsh and zcode workers are supported.")
    for settings in workers.values():
        if not isinstance(settings, dict):
            raise DispatchError("invalid_config", "Worker configuration must be a table.")
        for flag in ("enabled", "allow_write", "accept_provider_policy", "electron", "verbose"):
            if flag in settings and not isinstance(settings[flag], bool):
                raise DispatchError("invalid_config", f"{flag} must be boolean.")
        command = settings.get("command", [])
        if not isinstance(command, list) or not all(isinstance(s, str) and s for s in command):
            raise DispatchError("invalid_config", "command must be a nonempty-string array.")
        names = settings.get("env_allowlist", [])
        if not isinstance(names, list) or not all(isinstance(s, str) for s in names):
            raise DispatchError("invalid_config", "env_allowlist must be a string array.")
        for key in ("profile", "patch"):
            if key in settings and not isinstance(settings[key], str):
                raise DispatchError("invalid_config", f"{key} must be a string.")
        provider_config = settings.get("builtin_provider_config_file")
        if provider_config is not None and (
                not isinstance(provider_config, str) or not Path(provider_config).is_absolute()):
            raise DispatchError("invalid_config",
                                "builtin_provider_config_file must be an absolute path.")
    limit = data.get("max_output_bytes", DEFAULT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1024 <= limit <= 8 * DEFAULT_LIMIT:
        raise DispatchError("invalid_config", "max_output_bytes must be 1 KiB to 8 MiB.")
    timeout = data.get("max_timeout_seconds", 300)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 1800:
        raise DispatchError("invalid_config", "Invalid max_timeout_seconds.")
    return data
