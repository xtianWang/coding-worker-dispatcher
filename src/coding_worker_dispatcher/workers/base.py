from dataclasses import dataclass
from typing import Protocol
from ..models import Task


@dataclass
class Invocation:
    command: list[str]
    stdin: str
    env: dict[str, str]


class Adapter(Protocol):
    name: str
    def invocation(self, task: Task, settings: dict, command: list[str], env: dict) -> Invocation: ...
    def parse(self, stdout: str) -> str: ...
