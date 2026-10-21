# Factory

An agentic build/review loop for `devolutions-crypto`. A Python server watches
`factory/task/`, and for each task drives two Claude Code agents:

```
task file ──► Setup ──► ┌─ Attempt n ─────────────────────────────────┐ ──► Finalize
                        │ Builder ──► Collect changes ──► Reviewer    │
                        └──────────── retry (feedback) ◄──────────────┘
                          up to 1 attempt + 3 retries
```

1. **Setup** – creates branch `factory-task/<id>-<slug>` from `master` in a
   dedicated git worktree (`factory/data/worktrees/task-<id>`).
2. **Builder** (`agents/builder/`) – implements the task, commits on the
   branch, runs `agents/builder/tools/run_checks.py` (`cargo build` +
   `cargo test`) and writes `artifact.md`.
3. **Collect changes** – the server records `commits.txt` and `diff.patch`
   (auto-committing anything the builder left uncommitted).
4. **Reviewer** (`agents/reviewer/`) – checks the artifact proves the tests
   pass at the branch tip and re-runs the checks independently. It then
   reviews the code: it breaks the task into requirements, reads the changed
   code in context and marks each requirement `met` / `partial` / `missing`
   with evidence. It writes `review.json` with `accept` or `retry` + feedback.
5. A `retry` sends the feedback to the builder for the next attempt. The task
   ends **accepted** on the first accept, or **failed** after 3 retries.
   Before accepting, the server also applies a hard gate: the builder's
   `checks.json` must report success on a clean tree at the branch tip, the
   review must list requirements and all of them must be `met`, and no issue
   may be a blocker.

Accepted work stays on its branch (the worktree is removed); failed tasks keep
their worktree for inspection. Nothing is pushed.

## Layout

| Path | Content |
|------|---------|
| `task/` | Drop task files here (`.txt` / `.md`), see `task/README.md` |
| `agents/builder/` | `agent.json` (tools, permissions, timeout), `system_prompt.md`, `prompt_template.md`, `references/`, `tools/run_checks.py` |
| `agents/reviewer/` | Same structure; `references/review-checklist.md` defines `review.json` |
| `factory_server/` | Orchestrator, SQLite store, HTTP API and web UI (stdlib only) |
| `tests/` | `fake_claude.py` / `fake_cargo.py` for dry runs without an LLM |
| `data/` | Runtime state (git-ignored): `factory.db`, `runs/`, `worktrees/`, shared `cargo-target/` |

An agent's system prompt is its `system_prompt.md` followed by every file in
`references`; the per-task prompt is `prompt_template.md` with `$variables`
filled in. Both are saved next to each run and viewable in the UI.

## Running

Requires Python ≥ 3.10, git, cargo and the `claude` CLI (logged in) on PATH.

```bash
cd factory
python -m factory_server
```

or `uv run factory`. Open <http://127.0.0.1:8765>. Useful options:
`--base-ref`, `--max-retries`, `--port`, `--build-cmd`, `--test-cmd`,
`--claude-cmd`, `--keep-worktrees` (see `--help`).

The UI lists tasks with their status; selecting one shows its steps grouped by
phase (Setup, Attempt 1..n, Finalize). Click a step to see its live agent
transcript and every file it produced (artifact, checks, test output, diff,
review, prompts). Tasks can be cancelled or requeued.

### Dry run

Exercise the whole loop (including one retry) without an LLM or cargo:

```bash
python -m factory_server --claude-cmd "python tests/fake_claude.py" --build-cmd "python tests/fake_cargo.py build" --test-cmd "python tests/fake_cargo.py test"
```

## Security notes

Agents run headless with `--permission-mode acceptEdits` and the tool
allow-lists in each `agent.json`. The builder may run any `cargo`, `git` and
`python` command, so only feed the factory tasks you trust. The web UI has no
authentication and binds to `127.0.0.1` by default; keep it that way.
