"""Read-only candidate discovery; never installs or edits PATH."""
import os
from pathlib import Path
import shutil


def connection(worker, settings):
    result = dict(settings)
    if result.get("command"):
        result["connection_source"] = "configured"
        return result
    local = os.environ.get("LOCALAPPDATA")
    programs = Path(local) / "Programs" if local else None
    public = shutil.which(worker)
    if public and Path(public).suffix.lower() not in (".cmd", ".bat", ".ps1"):
        result.update(command=[public], connection_source="PATH")
        return result
    if worker == "dsh":
        install = programs / "DeepSeek Harness" if programs else None
        if public and Path(public).name.lower() == "dsh.cmd":
            candidate = Path(public).parents[4] if len(Path(public).parents) >= 5 else None
            if candidate and (candidate / "DeepSeek Harness.exe").is_file():
                install = candidate
        if install and (install / "DeepSeek Harness.exe").is_file() and (install / "resources/app.asar").is_file():
            result.update(command=[str(install / "DeepSeek Harness.exe"), "--expose-internals",
                                   str(install / "resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js")],
                          electron=True, connection_source="desktop_candidate")
    elif worker == "zcode":
        node = shutil.which("node")
        entry = programs / "ZCode/resources/glm/zcode.cjs" if programs else None
        if node and entry and entry.is_file():
            result.update(command=[node, str(entry)], connection_source="desktop_candidate")
    return result
