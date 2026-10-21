# Review checklist

## 1. Artifact completeness

- [ ] `artifact.md` exists and has every section required by the artifact format.
- [ ] `checks.json`, `build-output.txt`, `test-output.txt` exist.
- [ ] `diff.patch` is non-empty (the builder actually changed something).

## 2. Tests pass (according to the artifact)

- [ ] `checks.json.success` is `true`.
- [ ] Every step in `checks.json.steps` has `exit_code` 0.
- [ ] `tests.failed == 0` and `tests.passed > 0`.
- [ ] `test-output.txt` agrees with the totals (look for `test result:` lines,
      no `FAILED`, no `panicked`).
- [ ] `checks.json.dirty` is `false`.
- [ ] `checks.json.head` equals `git rev-parse HEAD` in the working directory.
      A mismatch means the checks ran on different code: verdict is `retry`.
- [ ] `artifact.md` Status matches `checks.json.success`.

## 3. Tests pass (independently)

- [ ] Your own checks run succeeded and its totals are consistent with the
      builder's (same number of failures: zero; passed count may differ only
      if you can explain why).

## 4. Code review against the task

- [ ] The task description is broken down into explicit requirements
      (listed in `review.json.requirements`).
- [ ] You read the changed files in full context in the working directory,
      not only the diff hunks.
- [ ] Each requirement is backed by code you inspected (cite file + function).
- [ ] Each testable requirement is exercised by a test whose assertions check
      the requested behaviour and would fail without the change.
- [ ] No change goes beyond the task scope without justification in the artifact.
- [ ] New code handles errors with `Result` (no `unwrap`/`panic!` on
      untrusted input) and follows the project guide conventions.

## 5. No cheating, no regressions

- [ ] `artifact.md` accurately describes what the code does (no claimed
      changes that are absent from the diff).
- [ ] No cheating: no removed tests, no `#[ignore]` added, no weakened
      assertions, no edits to existing vectors in `tests/conformity.rs`.
- [ ] No changes under `factory/`.
- [ ] No weakened crypto (parameters, validation, constant-time comparisons).
- [ ] On retries: every point of the previous feedback is addressed.

## review.json schema

```json
{
  "verdict": "accept",
  "tests_passing": true,
  "summary": "One paragraph describing what you verified and the outcome.",
  "requirements": [
    {
      "requirement": "Round-trip test for asymmetric encryption of an empty payload",
      "status": "met",
      "evidence": "src/ciphertext/mod.rs: test `asymmetric_empty_payload` encrypts b\"\" and asserts decrypt() is empty"
    }
  ],
  "issues": [
    {"severity": "blocker", "description": "..."}
  ],
  "feedback": "Actionable instructions for the builder (empty when accepting)."
}
```

- `verdict`: `"accept"` or `"retry"`.
- `tests_passing`: your conclusion on whether tests pass at the branch tip.
- `requirements`: one entry per requirement derived from the task;
  `status` is `"met"`, `"partial"` or `"missing"`; `evidence` cites the code
  and test you inspected (or what is absent).
- `issues[].severity`: `"blocker"` (forces retry) or `"minor"` (informational).
- A verdict of `accept` is invalid if `requirements` is empty, if any
  requirement is not `met`, or if any issue is a `blocker`. The factory
  server enforces this and turns such a verdict into a retry.
- When the verdict is `retry`, `feedback` must list each unmet requirement and
  each blocker with what to change.
