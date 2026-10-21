# Artifact format

This is the contract between the builder and the reviewer. Each attempt has
its own attempt directory, given in the task prompt. At the end of an attempt
it contains:

| File | Written by | Content |
|------|------------|---------|
| `checks.json` | checks script | Machine-readable results (see below) |
| `build-output.txt` | checks script | Full output of the build command |
| `test-output.txt` | checks script | Full output of the test command |
| `artifact.md` | builder | Human-readable report (see below) |
| `diff.patch` | factory server | `git diff <base>..HEAD` of the branch |
| `commits.txt` | factory server | `git log --oneline <base>..HEAD` |

The checks script is `factory/agents/builder/tools/run_checks.py`. Agents must
never hand-edit the files it produces.

## checks.json

```json
{
  "head": "<git commit SHA the checks ran against>",
  "dirty": false,
  "started_at": "2026-01-01T12:00:00Z",
  "steps": [
    {"name": "build", "command": "cargo build", "exit_code": 0, "duration_s": 12.3, "output_file": "build-output.txt"},
    {"name": "test",  "command": "cargo test",  "exit_code": 0, "duration_s": 40.1, "output_file": "test-output.txt",
     "tests": {"passed": 120, "failed": 0, "ignored": 2}}
  ],
  "success": true
}
```

Tests are considered passing only when **all** of the following hold:

- `success` is `true` and every step has `exit_code` 0;
- `tests.failed` is 0 and `tests.passed` is greater than 0;
- `dirty` is `false` and `head` equals the tip of the branch.

## artifact.md

Use exactly these sections:

```markdown
# Artifact: <task title>

## Status
PASS | FAIL  (copy from checks.json `success`)

## Summary
What was changed and why, in a few sentences.

## Changes
- `path/to/file.rs`: what changed
- ...

## Tests
- New/updated tests and what they cover.
- Totals from checks.json: passed / failed / ignored.

## Commits
Output of `git log --oneline <base>..HEAD`.

## Reviewer feedback addressed
(Retries only.) One bullet per point raised by the reviewer and how it was handled.

## Known limitations
Anything left undone, assumptions made, or follow-ups.
```
