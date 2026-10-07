from dataclasses import dataclass
import os
import signal
import subprocess
import threading
import time
from .models import DispatchError


@dataclass
class Execution:
    status: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool
    cleanup_confirmed: bool


def run(command, *, cwd, env, stdin="", timeout=300, max_bytes=1048576):
    """Capture a total bounded byte budget; no shell, raw logs, or automatic retry."""
    start = time.monotonic()
    job = None
    process = None
    threads = []
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    guard = threading.Lock()
    overflow = threading.Event()
    io_failed = threading.Event()
    used = 0
    status = "completed"
    if os.name == "nt":
        from .windows_job import Job
        job = Job()
    try:
        try:
            process = subprocess.Popen(
                command, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, shell=False,
                creationflags=(0x4 | 0x08000000) if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
            if job:
                job.attach_and_resume(process)
        except OSError:
            raise DispatchError("spawn_failed", "Worker could not be started.") from None

        def consume(name, pipe):
            nonlocal used
            try:
                while chunk := pipe.read(4096):
                    with guard:
                        room = max(0, max_bytes - used)
                        buffers[name].extend(chunk[:room])
                        used += min(room, len(chunk))
                        if len(chunk) > room:
                            overflow.set()
            except (OSError, ValueError):
                io_failed.set()
            finally:
                pipe.close()

        def feed():
            try:
                process.stdin.write(stdin.encode("utf-8"))
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                process.stdin.close()

        for name in ("stdout", "stderr"):
            thread = threading.Thread(target=consume, args=(name, getattr(process, name)), daemon=True)
            thread.start()
            threads.append(thread)
        writer = threading.Thread(target=feed, daemon=True)
        writer.start()
        threads.append(writer)
        try:
            while process.poll() is None:
                if overflow.is_set():
                    status = "output_limit"
                    break
                if time.monotonic() - start >= timeout:
                    status = "timed_out"
                    break
                time.sleep(0.01)
        except KeyboardInterrupt:
            status = "cancelled"
    finally:
        # Also dispose descendants when the root exits normally.
        if job:
            job.close()
        if process is not None:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                status = "cleanup_failed"
            for thread in threads:
                thread.join(timeout=2)
    if process is None:
        raise DispatchError("spawn_failed", "Worker did not start.")
    cleanup = process.poll() is not None and all(not t.is_alive() for t in threads)
    if not cleanup or io_failed.is_set():
        status = "cleanup_failed"
    elif overflow.is_set() and status == "completed":
        status = "output_limit"
    elif status == "completed" and process.returncode != 0:
        status = "failed"
    return Execution(status, process.returncode, buffers["stdout"].decode("utf-8", "replace"),
                     buffers["stderr"].decode("utf-8", "replace"),
                     int((time.monotonic() - start) * 1000), overflow.is_set(), cleanup)
