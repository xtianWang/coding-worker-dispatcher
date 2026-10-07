# coding-worker-dispatcher

An explicit local dispatcher for coding CLIs. The user or primary agent chooses
the worker; the dispatcher runs it and returns bounded JSON plus Git evidence.

**Status: experimental v0.1 implementation.** Offline tests use local fake workers.
The DSH read-only connectivity smoke test passed on Windows with DSH 0.2.0-rc.2:
the expected marker was returned, exit code was 0, and the dedicated Git fixture
and observed Git metadata were unchanged (2026-10-07, approximately 4.6 seconds).
The first invocation exposed a nested turn-reason parsing bug; the fix passed
the offline suite and the successful follow-up invocation. This verifies this
connectivity scenario, not filesystem isolation or general task correctness.
ZCode 0.16.9 started through the dispatcher but failed before producing a result
with `Select a model before continuing`. The adapter reports
`model_selection_unavailable` when that cause is visible in verbose output and
`model_creation_failed` for the shorter default error. Nothing is installed automatically.

## What works

- Explicit DSH / ZCode selection; `codex` returns control without starting a child.
- Trusted user TOML configuration and optional read-only executable discovery.
- Native executable + argument arrays, no shell command templates.
- Bounded stdout/stderr, timeout, cancellation and no retries.
- Windows Job Object lifetime management; POSIX process-group cleanup.
- DSH's built-in subagent spawn/fork tools disabled by a dispatcher-owned overlay.
- Worker JSON normalization and best-effort secret masking.
- Git clean-workspace gate for writes, pre/post content snapshots and diff checks.
- Per-repository dispatcher lock. No automatic commit, push, branch or rollback.

## Run without installing

Python 3.11+ and Git are required. There are no third-party runtime dependencies.
From the repository directory in PowerShell:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) "src")
python -m coding_worker_dispatcher workers --json
python -m unittest discover -s tests -v
```

The PYTHONPATH setting above affects this terminal only. For a conventional
installation in your own virtual environment, `python -m pip install -e .`
provides the `cwdispatch` command (build tooling may require a download).

## Configuration and discovery

Use `cwdispatch --config <trusted-config.toml> ...`, or the default user file:
Windows `%APPDATA%/coding-worker-dispatcher/config.toml`; otherwise
`~/.config/coding-worker-dispatcher/config.toml`.
No file is created on first run. Copy and edit `config.example.toml` yourself.

Connection precedence is configured command, native command on PATH, then known
Windows Desktop installation candidates. Discovery does not install anything,
edit PATH, read credentials, or establish permission support.

`command` is an argv prefix: executable plus optional runtime/entrypoint arguments.
It is never evaluated as shell text. Configure the native executable behind
.cmd/.bat/.ps1 launchers; these wrappers are rejected. Never place credentials
inside this array. A custom installation works by setting `command` explicitly.

Enabling execution requires all of:
- `external_dispatch = true`
- the selected worker's `enabled = true`
- approved absolute `allowed_roots`
- `accept_provider_policy = true`, acknowledging the limitations below
- for writes, `allow_write = true` plus this task's `--mode write`

There is no repository-local permission override, automatic fallback, or force flag.
`accept_provider_policy` is an acknowledgment, NOT a claim of sandbox verification.

## CLI

All current commands print JSON; `--json` is accepted explicitly for callers.

```text
cwdispatch workers --json
cwdispatch doctor --worker dsh --json
cwdispatch doctor --worker zcode --probe --json
cwdispatch run --worker codex --cwd <absolute-repo-root> --prompt "keep here"
cwdispatch run --worker dsh --cwd <absolute-repo-root> --prompt-stdin --json
cwdispatch run --worker zcode --cwd <absolute-repo-root> --mode write --allow-path src --prompt-stdin --json
cwdispatch run --task-stdin --json
```

`doctor` only resolves an executable unless `--probe` is supplied. A probe runs
`--version`; it does not submit a task or verify authentication, credits or sandboxing.
It can execute a configured binary even when task dispatch is disabled.

stdin is UTF-8. A Task contains `worker`, `prompt`, explicit absolute `cwd`,
optional `mode` (read_only), `timeout_seconds` (300), `allowed_paths`,
`task_id`, `schema_version` (1), and optional `task_type`.
Whole-Task input cannot be combined with field flags. ZCode currently transports
the prompt in argv even if the dispatcher received stdin; do not include secrets.

Result distinguishes `completed`, `failed`, `rejected`, `timed_out`,
`cancelled` and `delegated_back`. It includes original worker exit code,
bounded worker summary, changed files, Git evidence, limitations and errors.
`acceptance` remains `unknown`; tests are not independently run by this tool.
Worker output is data, never authorization to execute another command.

## Codex Skill entry point

The optional [Codex Skill](skills/coding-worker-dispatcher/SKILL.md) lets Codex call
the dispatcher after you explicitly select DSH or ZCode, then review its structured
result. Install it in your personal Codex skills directory and keep your checkout
path and trusted TOML config in private `dispatcher-home.txt` and
`dispatcher-config.txt` files beside the installed `SKILL.md` (one absolute path
per file). These local files are not part of this public repository. For example,
ask Codex: “Use `$coding-worker-dispatcher` with DSH to inspect this Git repository
in read-only mode, then verify and summarize the result.” The same dispatcher
config and Git safety checks apply when called from the Skill.

Exit codes: 0 protocol completion/handoff, 2 rejection, 3 failure, 4 timeout,
130 cancellation. Always inspect Result.status and Result.error as well.

## Compatibility

| Adapter | Locally inspected version | Input/output | Current permission support |
|---|---|---|---|
| DSH | 0.2.0-rc.2 | stdin / NDJSON | requests provider read-only or workspace-write; not certified isolation |
| ZCode | 0.16.9 | argv / JSON | headless model selection failed; read_only rejected; edit-mode writes experimental |
| codex | not applicable | no subprocess | return to primary agent |

The tested ZCode 0.16.9 headless `--prompt` path returned `Select a model before
continuing`, even though the desktop app could chat. Using the documented
`--surface desktop` option and the current built-in provider config path did not
resolve it. The dispatcher uses ZCode's documented `--json` flag, but cannot
select a model on the user's behalf; that requires a supported ZCode CLI path.
The same headless symptom is tracked in the [Z.AI feedback issue #744](https://github.com/zai-org/feedback/issues/744).

DSH Desktop's native launch path and ZCode's Node bundle have passed help/version
checks on Windows. Internal Desktop entrypoints can change between releases.
Other versions and POSIX execution have not been validated with real workers.

## Safety boundaries

Read [docs/safety.md](docs/safety.md) before enabling external workers.

This is a trusted-local-CLI execution and audit utility, not a hostile-code sandbox.
It does not enforce network isolation or prevent all worker commit/push operations.
allowed_paths is a postflight check, not a filesystem capability. A task can already
have caused side effects when a violation is reported. Output masking is best effort.

Write requires a clean Git root, including untracked files. Existing dirty read-only
workspaces are snapshotted by content. v0.1 rejects submodules, linked worktrees,
symlinks/junctions, hard links and repositories exceeding 10,000 observed files or
100 MiB observed content. Ignored files and outside paths are not audited.

Keep other agents and editors from writing during the run. The lock coordinates
this dispatcher only. Failures preserve modifications for inspection. There is
no stash, reset, clean, auto-apply, commit or push feature.

The dispatcher does not persist raw output or prompt logs. Workers may persist
their own sessions, load .env files, hooks, plugins and credentials. The DSH
adapter applies a packaged overlay after user overlays to disable DSH's built-in
subagent spawn/fork/control tools. This cannot prevent a custom plugin from
providing another agent-like route.
DSH Windows workspace-write can leave standing ACL/integrity-label changes;
initial real tests must use a disposable, dedicated workspace.

ZCode installations can place the CLI's built-in provider config at different
locations. For a `resources/glm/zcode.cjs` command, the adapter checks the current
`resources/config/provider/zcode-builtin.json` layout. If that is not found, set `builtin_provider_config_file`
under `[workers.zcode]` in your private config. The dispatcher passes this path
through ZCode's `ZCODE_BUILTIN_PROVIDER_CONFIG_FILE` override; keep local config
files out of version control. Set `verbose = true` there when diagnosing CLI
errors, or pass `--verbose` to the opt-in smoke script. Verbose output may contain
sensitive diagnostic details; the dispatcher's output masking is best effort.
Finding the built-in provider file does not itself select a model for headless runs.

## Testing and contributing

`python -m unittest discover -s tests -v` runs offline; tests initialize and commit
only inside disposable Git fixtures. They never execute real DSH/ZCode tasks.
The fake worker exercises both output pipes, timeout, descendant cleanup, malformed
protocols, redaction, dirty-workspace rejection and modifications after failures.
CI also builds a wheel and checks installation, the CLI entry point and the
packaged DSH safety overlay on Windows and Linux.

Real smoke tests are opt-in manual runs using a reviewed local config and a fresh
test repository. They may consume credits and persist worker sessions.
Use a harmless read-only DSH task first; independently verify files and output.
Do not run a ZCode write smoke test on a formal project.

No CI secrets are required. Do not commit local configs, logs, prompts or tokens.

## Next steps

ZCode's headless model-selection failure requires a CLI-side fix or supported
selection input. Reviewed minimal worker profiles, richer provider error
classification and measured acceptance outcomes can follow.
Observability and routing recommendations come later. No automatic routing,
daemon, MCP server, swarm, database, Web UI, nested-agent orchestration or cloud
deployment is included.

Future Skills should choose a worker explicitly, send a bounded task, parse Result,
and independently review changes. Workflow Skills (such as backend issue fixing)
remain separate from this execution mechanism.

### Opt-in DSH connectivity check

The prepared script creates a synthetic Git fixture under ignored `local/`, commits
only its synthetic baseline, then sends one read-only DSH task. It temporarily enables
dispatch in memory, preserves the fixture for review, and leaves saved configuration
unchanged. This can use model credits and store a DSH session. Run only after deciding
that the provider permission limits above are acceptable:

```powershell
python scripts/smoke_dsh.py --confirm-external-run
```

The check expects an exact marker answer and an unchanged Git workspace. It does not
prove that DSH cannot perform out-of-repository or network side effects.

### Opt-in ZCode connectivity check

The prepared script creates a synthetic Git fixture under ignored `local/`, commits
only its synthetic baseline, then sends one task through ZCode's edit mode. It
temporarily enables write dispatch in memory and grants one unused output path for
postflight checking. The task asks the worker to make no changes; the script reports
success only when the exact marker is returned and the fixture remains unchanged.
ZCode has no validated read-only mode, and allowed paths are checked after execution,
so this does not prevent writes or other side effects. The worker may use credits and
persist a session. Review the safety limits above before authorizing a run:

```powershell
python scripts/smoke_zcode.py --confirm-external-run
```

For installations where the built-in provider config is stored outside the
locations searched by ZCode, pass its absolute path with
`--provider-config <path>` or set `builtin_provider_config_file` in the private
worker config.

MIT licensed. See [LICENSE](LICENSE).
