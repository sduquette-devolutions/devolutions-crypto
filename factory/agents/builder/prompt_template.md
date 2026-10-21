# Task: $task_title

$task_text

---

## Execution context

- Attempt: **$attempt of $max_attempts**
- Working directory (git worktree): `$worktree`
- Branch (already checked out): `$branch`, created from `$base_ref`
- Attempt directory (write your artifact here): `$attempt_dir`
- Checks command (run from the working directory, after your final commit):

  ```
  $run_checks_cmd
  ```

$feedback_section

When you are done, `$attempt_dir/artifact.md` must exist and the working tree
must be clean. End your reply with a one-paragraph summary of what you did.
