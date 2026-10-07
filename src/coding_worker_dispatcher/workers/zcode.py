import json
from pathlib import Path
from .base import Invocation
from ..models import DispatchError


class Zcode:
    name = "zcode"

    def invocation(self, task, settings, command, env):
        # plan is not a validated filesystem sandbox. Fail closed for read_only.
        if task.mode == "read_only":
            raise DispatchError("permission_unsupported",
                                "ZCode read_only is not validated; plan is not a sandbox.")
        if len(task.prompt.encode("utf-8")) > 8192:
            raise DispatchError("invalid_task", "ZCode argv prompts are limited to 8 KiB.")
        if task.prompt.lstrip().startswith("/"):
            raise DispatchError("unsafe_request", "ZCode slash commands are not task prompts.")
        provider_config = settings.get("builtin_provider_config_file")
        if provider_config:
            if not Path(provider_config).is_file():
                raise DispatchError("invalid_config",
                                    "Configured ZCode built-in provider config file does not exist.")
            env = {**env, "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": provider_config}
        elif "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE" not in env:
            for part in command:
                entry = Path(part)
                if entry.name != "zcode.cjs" or entry.parent.name != "glm":
                    continue
                candidate = entry.parent.parent / "config" / "provider" / "zcode-builtin.json"
                if candidate.is_file():
                    env = {**env, "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": str(candidate)}
                break
        args = [*command, "--prompt", task.prompt, "--cwd", task.cwd,
                "--mode", "edit", "--json", "--no-color"]
        if settings.get("verbose", False):
            args.append("--verbose")
        return Invocation(args, "", env)

    def parse(self, stdout):
        try:
            data = json.loads(stdout)
            if not isinstance(data, dict):
                raise ValueError()
            # Exact fields are version-specific; unknown schemas fail closed.
            text = data.get("response")
            if not isinstance(text, str):
                raise ValueError()
            return text
        except (ValueError, RecursionError):
            raise DispatchError("malformed_output", "Unsupported ZCode JSON summary schema.") from None

    def classify_failure(self, stderr):
        if "Select a model before continuing" in stderr:
            return DispatchError(
                "model_selection_unavailable",
                "ZCode headless CLI did not provide a model selection; the dispatcher has no supported CLI option to choose one.",
            )
        if "Model creation failed" in stderr:
            return DispatchError(
                "model_creation_failed",
                "ZCode could not create a model. Check its CLI model/provider setup; use verbose diagnostics privately for the underlying cause.",
            )
        return None
