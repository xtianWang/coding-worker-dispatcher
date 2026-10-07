from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from .models import DispatchError
from .runner import run
from .safety import inside, linked

MAX_FILES = 10000
MAX_TOTAL = 100 * 1024 * 1024


def git(cwd, *args, allow_failure=False):
    executable = shutil.which("git")
    if not executable:
        raise DispatchError("git_unavailable", "Git is required for external execution.")
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GIT_PAGER="cat")
    result = run([executable, "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
                  "-c", "core.quotePath=false", *args],
                 cwd=cwd, env=env, timeout=10, max_bytes=8 * 1024 * 1024)
    if result.status not in ("completed", "failed") or not result.cleanup_confirmed:
        raise DispatchError("git_check_failed", "Git inspection exceeded its execution limits.")
    if result.exit_code and not allow_failure:
        raise DispatchError("git_check_failed", "Git inspection failed.")
    return result.stdout, result.exit_code


def repository(cwd):
    root, _ = git(cwd, "rev-parse", "--show-toplevel")
    root = Path(root.strip()).resolve()
    if cwd != root:
        raise DispatchError("invalid_cwd", "v0.1 requires cwd to be the repository root.")
    gitdir, _ = git(root, "rev-parse", "--absolute-git-dir")
    metadata = Path(gitdir.strip()).resolve()
    # Keep initial support small: linked worktrees and submodules are deferred.
    if metadata != root / ".git" or linked(root / ".git"):
        raise DispatchError("unsupported_repository", "Linked worktrees and submodules are not supported yet.")
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        if (metadata / marker).exists():
            raise DispatchError("unsafe_request", "Repository has an operation in progress.")
    return root, metadata


@contextmanager
def workspace_lock(root):
    directory = Path(tempfile.gettempdir()) / "coding-worker-dispatcher-locks"
    directory.mkdir(exist_ok=True)
    key = hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()
    path = directory / (key + ".lock")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise DispatchError("workspace_busy", "Dispatcher lock exists; inspect stale locks manually.") from None
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        fd = None
        yield
    finally:
        if fd is not None:
            os.close(fd)
        path.unlink(missing_ok=True)


def snapshot(root, metadata):
    status, _ = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    head, _ = git(root, "rev-parse", "--verify", "HEAD")
    refs, _ = git(root, "show-ref", allow_failure=True)
    branch, _ = git(root, "symbolic-ref", "-q", "HEAD", allow_failure=True)
    names, _ = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    paths = sorted(set(filter(None, names.split("\0"))))
    if len(paths) > MAX_FILES or "\ufffd" in names:
        raise DispatchError("snapshot_limit", "Repository exceeds file-count/filename support limits.")
    manifest = {}
    total = 0
    for name in paths:
        path = root / name
        if not inside(path.resolve(), root):
            raise DispatchError("unsafe_request", "Repository contains a path outside its root.")
        if linked(path) or any(linked(p) for p in path.parents if inside(p, root)):
            raise DispatchError("unsafe_request", "Repository links are unsupported.")
        if not path.exists():
            continue
        if not path.is_file() or path.stat().st_nlink > 1:
            raise DispatchError("unsupported_repository", "Submodules, special files and hard links are unsupported.")
        stat = path.stat()
        total += stat.st_size
        if total > MAX_TOTAL:
            raise DispatchError("snapshot_limit", "Repository snapshot exceeds 100 MiB.")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(65536):
                digest.update(chunk)
        manifest[name] = (digest.hexdigest(), stat.st_mode)
    index = metadata / "index"
    index_hash = hashlib.sha256(index.read_bytes()).hexdigest() if index.exists() else None
    return {"status": status, "head": head, "refs": refs, "branch": branch,
            "index": index_hash, "files": manifest}


def compare(root, before, after, mode, targets):
    changed = sorted(name for name in before["files"].keys() | after["files"].keys()
                     if before["files"].get(name) != after["files"].get(name))
    metadata_changed = [key for key in ("head", "refs", "branch", "index")
                        if before[key] != after[key]]
    outside = [name for name in changed if not any(inside((root / name).resolve(), p) for p in targets)]
    _, diff_code = git(root, "diff", "--no-ext-diff", "--no-textconv", "--check", allow_failure=True)
    violations = []
    if metadata_changed:
        violations.append("git_metadata_changed")
    if mode == "read_only" and changed:
        violations.append("read_only_violation")
    if mode == "write" and outside:
        violations.append("outside_allowed_paths")
    if diff_code:
        violations.append("diff_check_failed")
    return changed, {"checked": True, "baseline_dirty": bool(before["status"]),
                     "metadata_changed": metadata_changed, "outside_allowed_paths": outside,
                     "diff_check_passed": diff_code == 0, "violations": violations,
                     "scope": "tracked and non-ignored untracked files; not a complete filesystem audit"}
