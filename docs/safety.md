# Safety and operational contract

## Trust model

Trust Python, this dispatcher, your user config, installed workers and their
dependencies. Treat repository instructions, prompts and returned model text as
untrusted data. This project is not containment for malicious installed binaries.
No model output is interpreted as a dispatcher command.

A configured executable can do anything the user account can do. The
accept_provider_policy flag does not elevate protection; it records acceptance
of the experimental provider-managed permission boundary. No unsafe override
can silently convert ZCode plan mode into supported read_only.

## Layered controls

- Preflight: configuration, explicit worker, mode, native launcher, cwd, Git state.
- Execution: no shell interpolation, bounded output, deadlines and process lifetime.
- Provider: DSH receives an explicit file-effect mode; ZCode write receives edit.
- Postflight: content fingerprints, HEAD, refs, branch, index and diff --check.
- Acceptance: always performed separately by the user or primary agent.

Provider configuration can load hooks and plugins. The DSH adapter appends a
dispatcher-owned packaged patch after configured overlays, disabling its shipped
subagent spawn/fork/control rows for each dispatch. Custom plugins can still add
other agent-like tools; the entire plugin graph is not attested.

## Git evidence

Git is invoked with optional locks and fsmonitor disabled. External diff and
textconv are disabled for diff checking. Snapshots observe tracked/non-ignored
untracked files, not reads, transient modifications, ignored files or remote state.
No complete forensic or security audit is claimed. A successful diff --check
does not prove correctness and does not inspect untracked-file whitespace.

No rollback is performed. A timed-out or failed task may have written files.
Retries are disabled. Inspect the original result and workspace before deciding
whether to run again. Dirty writes are refused instead of automatically stashing.

Locks live in the system temp directory under coding-worker-dispatcher-locks.
After a crash, inspect the recorded PID and active processes before manually
removing the exact stale lock. Never automatically delete a lock based on age alone.

## Windows

The runner starts a suspended native process, assigns it to a KILL_ON_JOB_CLOSE
Job Object, and resumes it. A job assignment failure rejects execution rather than
running uncontained. Closing the job ends its descendants; this controls lifetime,
not filesystem or network access. NtResumeProcess is platform-specific and covered
by local process tests; more Windows versions need validation.

Do not infer worker sandbox guarantees from the Job Object. DSH's own write mode
can alter ACLs and low-integrity labels beyond the run's lifetime. This dispatcher
does not attempt to reverse those changes. Use dedicated test copies first.

## Secrets and input

Known credential environment values and common Authorization/Bearer/API-key/JWT/
cookie forms are masked before summaries/excerpts return. Unknown, encoded,
split or transformed secrets can escape detection. No raw logs are persisted.

Allowed environment names come from trusted config, alongside basic system paths.
Worker processes can still load their own credentials and project .env files.
Removing environment variables does not prevent file-based credential access.
Do not include secrets in prompts, particularly ZCode argv prompts.

The parser accepts only a bounded output capture. Exceeding the aggregate byte
budget stops the process; truncation is not silently accepted as task completion.
Tests listed by a worker are not promoted to verified test results.

## Scope of current validation

Offline subprocess and Git tests do not prove real worker permissions.
Help/version checks do not prove auth, available quota, correct edits, stable
plugin behavior or absence of persistent side effects. Real-task validation
must be reported separately with the version, platform and acceptance evidence.
