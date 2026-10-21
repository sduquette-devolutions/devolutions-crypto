# Review request: $task_title

## Original task

$task_text

---

## Execution context

- Attempt under review: **$attempt of $max_attempts**
- Working directory (git worktree, branch `$branch`, based on `$base_ref`): `$worktree`
- Builder attempt directory (artifact, checks, diff): `$attempt_dir`
- Review directory (write `review.json` and your checks here): `$review_dir`
- Independent checks command (run from the working directory):

  ```
  $run_checks_cmd
  ```

$feedback_section

Write `$review_dir/review.json` with your verdict and end your reply with the
same JSON in a ```json fenced block.
