"""Deterministic local process, never calls a model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
if mode == "--version":
    print("0.2.0-rc.2")
    sys.exit(0)
elif mode == "timeout":
    time.sleep(30)
elif mode == "flood":
    for _ in range(1000):
        sys.stdout.write("x" * 4096)
        sys.stdout.flush()
        sys.stderr.write("y" * 4096)
        sys.stderr.flush()
elif mode == "nonzero":
    print("failure", file=sys.stderr)
    sys.exit(7)
elif mode == "model_missing":
    print("Error: Model creation failed\nCause: Error: Select a model before continuing",
          file=sys.stderr)
    sys.exit(1)
elif mode == "child":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    Path(sys.argv[2]).write_text(str(child.pid))
    time.sleep(30)
elif mode == "stdin":
    print(sys.stdin.read())
elif mode == "broken":
    print("not JSON")
elif mode in ("write", "outside"):
    Path("a.txt" if mode == "write" else "b.txt").write_bytes(b"worker change\n")
    print(json.dumps({"type": "status", "phase": "turn_end", "reason": {"kind": "completed"}}))
    print(json.dumps({"type": "final", "text": "Updated a file."}))
elif mode == "write_fail":
    Path("a.txt").write_bytes(b"partial change\n")
    sys.exit(7)
elif mode == "secret":
    print(json.dumps({"type": "status", "phase": "turn_end", "reason": {"kind": "completed"}}))
    print(json.dumps({"type": "final", "text": "Authorization: Bearer synthetic-secret-value"}))
else:
    sys.stdin.read()
    print(json.dumps({"type": "session", "id": "fake"}))
    print(json.dumps({"type": "status", "phase": "turn_end", "reason": {"kind": "completed"}}))
    print(json.dumps({"type": "final", "text": "Inspected successfully."}))
