"""Check that a built wheel installs and retains its safety overlay and CLI."""
import argparse
import configparser
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", nargs="?", type=Path,
                        help="Wheel to check; defaults to the sole wheel in dist/")
    args = parser.parse_args()
    if args.wheel is None:
        wheels = list(Path("dist").glob("coding_worker_dispatcher-*.whl"))
        if len(wheels) != 1:
            parser.error("Expected exactly one coding-worker-dispatcher wheel in dist/.")
        wheel = wheels[0]
    else:
        wheel = args.wheel
    wheel = wheel.resolve(strict=True)

    overlay = "coding_worker_dispatcher/workers/dsh-no-subagents.yml"
    with zipfile.ZipFile(wheel) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Wheel contains a damaged file.")
        names = set(archive.namelist())
        if overlay not in names:
            raise RuntimeError("Wheel is missing the DSH safety overlay.")
        entry_points = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        if len(entry_points) != 1:
            raise RuntimeError("Wheel has no unique entry-point metadata.")
        metadata = configparser.ConfigParser()
        metadata.read_string(archive.read(entry_points[0]).decode("utf-8"))
        if metadata.get("console_scripts", "cwdispatch", fallback=None) != "coding_worker_dispatcher.cli:main":
            raise RuntimeError("Wheel has an unexpected cwdispatch entry point.")

    with tempfile.TemporaryDirectory(prefix="cwdispatch-wheel-") as temporary:
        target = Path(temporary) / "installed"
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
             "--disable-pip-version-check", "--target", str(target), str(wheel)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        env = {**os.environ, "PYTHONPATH": str(target)}
        probe = subprocess.run(
            [sys.executable, "-c",
             "import json; from pathlib import Path; import coding_worker_dispatcher as p; "
             "from importlib.resources import files; "
             "print(json.dumps({'package': str(Path(p.__file__).resolve()), "
             "'overlay': files(p).joinpath('workers', 'dsh-no-subagents.yml').is_file()}))"],
            cwd=temporary, env=env, check=True, capture_output=True, text=True,
        )
        installed = json.loads(probe.stdout)
        if not Path(installed["package"]).is_relative_to(target.resolve()) or not installed["overlay"]:
            raise RuntimeError("Installed package or DSH safety overlay is missing.")
        cli = subprocess.run(
            [sys.executable, "-m", "coding_worker_dispatcher", "workers", "--json"],
            cwd=temporary, env=env, check=True, capture_output=True, text=True,
        )
        names = {item["name"] for item in json.loads(cli.stdout)["workers"]}
        if names != {"codex", "dsh", "zcode"}:
            raise RuntimeError("Installed CLI did not list all worker choices.")
    print("Wheel installation, DSH overlay, and CLI check passed.")


if __name__ == "__main__":
    main()
