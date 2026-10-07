from dataclasses import asdict, dataclass, field
import math
from pathlib import Path
import uuid


class DispatchError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Task:
    worker: str
    prompt: str
    cwd: str
    mode: str = "read_only"
    timeout_seconds: float = 300
    allowed_paths: list[str] = field(default_factory=list)
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    schema_version: int = 1
    task_type: str | None = None

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise DispatchError("invalid_task", "Task must be an object.")
        try:
            task = cls(**value)
        except TypeError:
            raise DispatchError("invalid_task", "Missing or unknown Task fields.") from None
        task.validate()
        return task

    def validate(self):
        if self.schema_version != 1 or isinstance(self.schema_version, bool):
            raise DispatchError("invalid_task", "Unsupported schema_version.")
        if not isinstance(self.worker, str) or self.worker not in ("codex", "dsh", "zcode"):
            raise DispatchError("invalid_task", "Unknown worker.")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise DispatchError("invalid_task", "Prompt must not be blank.")
        if len(self.prompt.encode("utf-8")) > 65536:
            raise DispatchError("invalid_task", "Prompt exceeds 64 KiB.")
        if not isinstance(self.cwd, str) or not self.cwd or not Path(self.cwd).is_absolute():
            raise DispatchError("invalid_cwd", "cwd must be an explicit absolute path.")
        if self.mode not in ("read_only", "write"):
            raise DispatchError("invalid_task", "Unknown mode.")
        if (isinstance(self.timeout_seconds, bool)
                or not isinstance(self.timeout_seconds, (int, float))
                or not math.isfinite(self.timeout_seconds)
                or not 0 < self.timeout_seconds <= 1800):
            raise DispatchError("invalid_task", "Timeout must be between 0 and 1800 seconds.")
        if not isinstance(self.allowed_paths, list) or not all(
                isinstance(p, str) and p for p in self.allowed_paths):
            raise DispatchError("invalid_task", "allowed_paths must be a list of paths.")
        if self.mode == "read_only" and self.allowed_paths:
            raise DispatchError("invalid_task", "read_only cannot grant writable paths.")
        if self.mode == "write" and not self.allowed_paths:
            raise DispatchError("invalid_task", "write requires allowed_paths.")
        if not isinstance(self.task_id, str) or not 1 <= len(self.task_id) <= 100:
            raise DispatchError("invalid_task", "Invalid task_id.")
        if self.task_type is not None and (
                not isinstance(self.task_type, str) or len(self.task_type) > 100):
            raise DispatchError("invalid_task", "Invalid task_type.")


@dataclass
class Result:
    worker: str
    task_id: str
    schema_version: int = 1
    status: str = "rejected"
    summary: str = ""
    summary_source: str = "dispatcher"
    error: dict | None = None
    exit_code: int | None = None
    duration_ms: int = 0
    attempts: int = 0
    files_changed: list[str] = field(default_factory=list)
    tests: list = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    truncated: bool = False
    output_excerpt: dict = field(default_factory=dict)
    log_reference: str | None = None
    git: dict = field(default_factory=dict)
    safety: dict = field(default_factory=dict)
    acceptance: str = "unknown"

    def to_dict(self):
        return asdict(self)
