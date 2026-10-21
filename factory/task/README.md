# Tasks

Drop one `.txt` (or `.md`) file per task in this folder. The factory server
picks up new files automatically (every few seconds) and queues them in order.

- The **first non-empty line** is the task title (it is also used for the
  branch name `factory-task/<id>-<slug>`).
- The rest is the description given verbatim to the builder and reviewer.
- A file is processed once; tasks are identified by file name. To run a task
  again, use **Requeue** in the web UI or add a new file.
- This README is ignored.

Be specific about the expected behaviour and how it should be tested; the
reviewer accepts only work that has passing tests and accomplishes the task.
