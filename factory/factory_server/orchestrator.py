"""The factory loop: pick tasks, run builder -> collect changes -> reviewer, retry."""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import IO

from . import gitops
from .agents import AgentDefinition, AgentResult, AgentRunner
from .config import RUN_CHECKS_SCRIPT, Settings
from .db import Database, now

log = logging.getLogger("factory")

TASK_SUFFIXES = {".txt", ".md"}


class Cancelled(Exception):
    pass


class Orchestrator:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.runner = AgentRunner(settings.resolve_claude_cmd())
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._current_task: int | None = None
        self._cancel_task: int | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self.db.recover_interrupted()
        threading.Thread(target=self._watch_loop, name="task-watcher", daemon=True).start()
        threading.Thread(target=self._work_loop, name="task-worker", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self.runner.cancel()

    # -- controls exposed to the web UI -------------------------------------

    def cancel(self, task_id: int) -> bool:
        task = self.db.get_task(task_id)
        if not task:
            return False
        if task["status"] == "queued":
            self.db.update_task(task_id, status="cancelled", finished_at=now())
            return True
        if self._current_task == task_id:
            self._cancel_task = task_id
            self.runner.cancel()
            return True
        return False

    def requeue(self, task_id: int) -> bool:
        task = self.db.get_task(task_id)
        if not task or task["status"] in ("queued", "running"):
            return False
        self.db.update_task(task_id, status="queued", error=None, summary=None, finished_at=None)
        self._wake.set()
        return True

    # -- task discovery ------------------------------------------------------

    def scan_tasks(self) -> list[int]:
        known = self.db.known_filenames()
        added = []
        for path in sorted(self.settings.task_dir.iterdir()):
            if (
                not path.is_file()
                or path.suffix.lower() not in TASK_SUFFIXES
                or path.name.lower() == "readme.md"
                or path.name in known
            ):
                continue
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if not content:
                continue
            title = next((ln.strip().lstrip("#").strip() for ln in content.splitlines() if ln.strip()), path.stem)
            task_id = self.db.add_task(path.name, title[:200], content, self.settings.max_attempts)
            if task_id:
                log.info("registered task %s from %s", task_id, path.name)
                added.append(task_id)
        if added:
            self._wake.set()
        return added

    def _watch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_tasks()
            except Exception:
                log.exception("task scan failed")
            self._stop.wait(self.settings.poll_interval)

    def _work_loop(self) -> None:
        while not self._stop.is_set():
            task = self.db.next_queued_task()
            if not task:
                self._wake.wait(self.settings.poll_interval)
                self._wake.clear()
                continue
            self._current_task = task["id"]
            try:
                self._process(task)
            except Cancelled:
                self.db.update_task(task["id"], status="cancelled", finished_at=now())
                log.info("task %s cancelled", task["id"])
            except Exception as exc:
                log.exception("task %s errored", task["id"])
                self.db.update_task(task["id"], status="error", error=str(exc), finished_at=now())
            finally:
                self._current_task = None
                self._cancel_task = None

    # -- the loop for one task -----------------------------------------------

    def _process(self, task: dict) -> None:
        s = self.settings
        task_id = task["id"]
        self.db.update_task(task_id, status="running", started_at=now(), error=None)
        task_dir = s.runs_dir / f"task-{task_id}"
        task_dir.mkdir(parents=True, exist_ok=True)

        branch = task["branch"] or f"factory-task/{task_id}-{gitops.slugify(task['title'])}"
        worktree = s.worktrees_dir / f"task-{task_id}"
        base_sha = self._setup(task, task_dir, branch, worktree)

        builder = AgentDefinition.load("builder")
        reviewer = AgentDefinition.load("reviewer")
        feedback = self.db.last_review_feedback(task_id)
        first = self.db.max_attempt(task_id) + 1
        last = first + s.max_attempts - 1
        prev_attempt_dir: Path | None = None

        for attempt in range(first, last + 1):
            attempt_dir = task_dir / f"attempt-{attempt}"
            review_dir = attempt_dir / "review"
            review_dir.mkdir(parents=True, exist_ok=True)
            self.db.update_task(task_id, attempts_used=attempt - first + 1)
            common = {
                "task_title": task["title"],
                "task_text": task["content"],
                "attempt": str(attempt - first + 1),
                "max_attempts": str(s.max_attempts),
                "branch": branch,
                "base_ref": f"{s.base_ref} ({base_sha[:12]})",
                "worktree": worktree.as_posix(),
                "attempt_dir": attempt_dir.as_posix(),
                "review_dir": review_dir.as_posix(),
            }

            # 1. Builder
            builder_fb = ""
            if feedback:
                builder_fb = (
                    "## Reviewer feedback on the previous attempt (must be addressed)\n\n"
                    f"{feedback}\n"
                )
                if prev_attempt_dir:
                    builder_fb += f"\nPrevious attempt directory: `{prev_attempt_dir.as_posix()}`\n"
            prompt = builder.render_prompt(
                **common, feedback_section=builder_fb, run_checks_cmd=self._checks_cmd(attempt_dir)
            )
            _, build = self._run_agent_step(
                task_id, attempt, "build", "Builder", builder, prompt, worktree, attempt_dir,
                [attempt_dir], ["artifact.md", "checks.json", "test-output.txt", "build-output.txt"],
            )

            # 2. Collect what the builder left on the branch
            self._collect_changes(task_id, attempt, worktree, attempt_dir, base_sha)

            # 3. Reviewer
            if not (attempt_dir / "artifact.md").exists() or not (attempt_dir / "checks.json").exists():
                missing = [f for f in ("artifact.md", "checks.json") if not (attempt_dir / f).exists()]
                feedback = (
                    f"The attempt did not produce {', '.join(missing)}. Run the checks script after "
                    "your final commit and write artifact.md as described in the artifact format."
                )
                if build.is_error:
                    feedback += f"\nThe builder run ended with an error: {build.result_text[:1000]}"
                step = self.db.add_step(task_id, attempt, "review", "Reviewer", "reviewer")
                self.db.finish_step(
                    step, "skipped", verdict="retry", summary="Skipped: artifact incomplete. " + feedback,
                    meta={"feedback": feedback},
                )
            else:
                reviewer_fb = (
                    f"## Feedback given to the builder after the previous attempt\n\n{feedback}\n"
                    if feedback else ""
                )
                prompt = reviewer.render_prompt(
                    **common, feedback_section=reviewer_fb, run_checks_cmd=self._checks_cmd(review_dir)
                )
                verdict, feedback = self._review(
                    task_id, attempt, reviewer, prompt, worktree, attempt_dir, review_dir
                )
                if verdict == "accept":
                    self._finalize(task_id, branch, worktree, accepted=True, attempts=attempt - first + 1)
                    return
            prev_attempt_dir = attempt_dir

        self._finalize(task_id, branch, worktree, accepted=False, attempts=s.max_attempts)

    def _checks_cmd(self, out_dir: Path) -> str:
        return f'python "{RUN_CHECKS_SCRIPT.as_posix()}" "{out_dir.as_posix()}"'

    def _agent_env(self) -> dict[str, str]:
        return {
            "CARGO_TARGET_DIR": str(self.settings.cargo_target_dir),
            "FACTORY_BUILD_CMD": self.settings.build_cmd,
            "FACTORY_TEST_CMD": self.settings.test_cmd,
        }

    def _rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.settings.runs_dir.resolve()).as_posix()

    def _check_cancel(self, task_id: int) -> None:
        if self._cancel_task == task_id or self._stop.is_set():
            raise Cancelled()

    # -- steps ---------------------------------------------------------------

    def _text_step(self, task_id: int, attempt: int, kind: str, title: str, log_file: Path) -> tuple[int, IO[str]]:
        step = self.db.add_step(task_id, attempt, kind, title)
        self.db.update_step(step, log_path=self._rel(log_file), log_format="text")
        return step, open(log_file, "a", encoding="utf-8")

    def _setup(self, task: dict, task_dir: Path, branch: str, worktree: Path) -> str:
        s = self.settings
        step, logf = self._text_step(task["id"], 0, "setup", "Prepare branch & worktree", task_dir / "setup.log")
        with logf:
            try:
                logf.write(f"[{now()}] repository: {s.repo_root}\n")
                base_sha = task["base_sha"] or gitops.git(
                    s.repo_root, "rev-parse", "--verify", f"{s.base_ref}^{{commit}}", log=logf
                )
                how = gitops.ensure_worktree(s.repo_root, worktree, branch, base_sha, logf)
            except Exception as exc:
                logf.write(f"ERROR: {exc}\n")
                self.db.finish_step(step, "failed", summary=str(exc))
                raise
        self.db.update_task(
            task["id"], branch=branch, worktree=str(worktree), base_ref=s.base_ref, base_sha=base_sha
        )
        self.db.finish_step(
            step, "succeeded", summary=f"{how}: {branch} @ {base_sha[:12]} in {worktree.name}",
            meta={"branch": branch, "base_sha": base_sha, "worktree": str(worktree)},
        )
        return base_sha

    def _run_agent_step(
        self, task_id: int, attempt: int, kind: str, title: str, agent: AgentDefinition,
        prompt: str, cwd: Path, out_dir: Path, add_dirs: list[Path], outputs: list[str],
    ) -> tuple[int, AgentResult]:
        self._check_cancel(task_id)
        step = self.db.add_step(task_id, attempt, kind, title, agent.name)
        transcript = out_dir / f"{agent.name}.transcript.jsonl"
        files = [out_dir / f"{agent.name}.prompt.md", out_dir / f"{agent.name}.system_prompt.md"]
        self.db.update_step(
            step, log_path=self._rel(transcript), log_format="stream-json",
            files=[self._rel(f) for f in files],
        )
        try:
            result = self.runner.run(agent, prompt, cwd, out_dir, add_dirs, self._agent_env())
        except Exception as exc:
            self.db.finish_step(step, "failed", summary=f"failed to launch agent: {exc}")
            raise
        produced = [out_dir / name for name in outputs if (out_dir / name).exists()]
        status = "failed" if result.is_error else "succeeded"
        summary = result.result_text.strip()
        if result.timed_out:
            summary = "Timed out. " + summary
        self.db.finish_step(
            step, status, exit_code=result.exit_code, cost_usd=result.cost_usd,
            summary=summary[:4000], files=[self._rel(f) for f in produced + files],
            meta={"num_turns": result.num_turns, "timed_out": result.timed_out},
        )
        if result.cancelled:
            raise Cancelled()
        return step, result

    def _collect_changes(self, task_id: int, attempt: int, worktree: Path, attempt_dir: Path, base_sha: str) -> None:
        self._check_cancel(task_id)
        step, logf = self._text_step(task_id, attempt, "changes", "Collect branch changes", attempt_dir / "changes.log")
        notes = []
        with logf:
            try:
                if gitops.git(worktree, "status", "--porcelain", log=logf):
                    gitops.git(worktree, "add", "-A", log=logf)
                    gitops.git(
                        worktree, "commit", "-m",
                        f"factory: uncommitted changes left by builder (attempt {attempt})", log=logf,
                    )
                    notes.append("auto-committed uncommitted changes")
                commits = gitops.git(worktree, "log", "--oneline", f"{base_sha}..HEAD", log=logf)
                diff = gitops.git(worktree, "diff", f"{base_sha}..HEAD")
                stat = gitops.git(worktree, "diff", "--shortstat", f"{base_sha}..HEAD", log=logf)
                head = gitops.git(worktree, "rev-parse", "HEAD", log=logf)
            except Exception as exc:
                logf.write(f"ERROR: {exc}\n")
                self.db.finish_step(step, "failed", summary=str(exc))
                raise
        (attempt_dir / "commits.txt").write_text(commits + "\n", encoding="utf-8")
        (attempt_dir / "diff.patch").write_text(diff + "\n", encoding="utf-8")
        n = len(commits.splitlines())
        summary = f"{n} commit(s) on branch; {stat or 'no changes'}; HEAD {head[:12]}"
        if notes:
            summary += f" ({'; '.join(notes)})"
        self.db.finish_step(
            step, "succeeded" if n else "failed", summary=summary,
            files=[self._rel(attempt_dir / "commits.txt"), self._rel(attempt_dir / "diff.patch")],
            meta={"head": head, "commits": n},
        )

    def _review(
        self, task_id: int, attempt: int, reviewer: AgentDefinition, prompt: str,
        worktree: Path, attempt_dir: Path, review_dir: Path,
    ) -> tuple[str, str]:
        step, result = self._run_agent_step(
            task_id, attempt, "review", "Reviewer", reviewer, prompt, worktree, review_dir,
            [attempt_dir], ["review.json", "checks.json", "test-output.txt", "build-output.txt"],
        )
        review = _load_review(review_dir / "review.json", result.result_text)
        if review is None:
            verdict = "retry"
            feedback = ("The reviewer could not produce a valid verdict. Make sure artifact.md, "
                        "checks.json and the test outputs are complete and consistent.")
            summary = "No valid review.json produced by the reviewer."
            review = {}
        else:
            verdict = review["verdict"]
            feedback = str(review.get("feedback") or "").strip()
            summary = str(review.get("summary") or "").strip()

        gate_issues = self._gate(review, attempt_dir, worktree) if verdict == "accept" else []
        if gate_issues:
            verdict = "retry"
            feedback = (feedback + "\n\n" if feedback else "") + "Factory gate rejected the acceptance:\n" + \
                "\n".join(f"- {i}" for i in gate_issues)
            summary += " [overridden by factory gate: " + "; ".join(gate_issues) + "]"

        self.db.update_step(step, verdict=verdict, summary=summary[:4000], meta={
            **self.db.get_step(step)["meta"], "feedback": feedback, "review": review, "gate_issues": gate_issues,
        })
        return verdict, feedback

    def _gate(self, review: dict, attempt_dir: Path, worktree: Path) -> list[str]:
        """Hard checks that an accepted attempt really has passing tests at the branch tip."""
        issues = []
        blockers = [i for i in review.get("issues", []) if isinstance(i, dict) and i.get("severity") == "blocker"]
        if blockers:
            issues.append(f"reviewer listed {len(blockers)} blocker issue(s)")
        if review.get("tests_passing") is False:
            issues.append("reviewer reported tests_passing=false")
        requirements = [r for r in review.get("requirements") or [] if isinstance(r, dict)]
        if not requirements:
            issues.append("reviewer did not list the task requirements it verified")
        unmet = [r for r in requirements if r.get("status") != "met"]
        if unmet:
            issues.append("requirement(s) not met: " + "; ".join(
                f"{r.get('requirement', '?')} ({r.get('status', '?')})" for r in unmet))
        try:
            checks = json.loads((attempt_dir / "checks.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return issues + [f"checks.json unreadable: {exc}"]
        head = gitops.git(worktree, "rev-parse", "HEAD")
        if not checks.get("success"):
            issues.append("checks.json success is false")
        if checks.get("dirty"):
            issues.append("checks ran on a dirty working tree")
        if checks.get("head") != head:
            issues.append(f"checks ran on {str(checks.get('head'))[:12]}, branch tip is {head[:12]}")
        return issues

    def _finalize(self, task_id: int, branch: str, worktree: Path, accepted: bool, attempts: int) -> None:
        s = self.settings
        task_dir = s.runs_dir / f"task-{task_id}"
        step, logf = self._text_step(task_id, 0, "finalize", "Finalize", task_dir / "finalize.log")
        with logf:
            if accepted:
                summary = f"Accepted after {attempts} attempt(s). Changes are on branch {branch}."
                if not s.keep_worktrees:
                    gitops.remove_worktree(s.repo_root, worktree, logf)
            else:
                summary = (f"Rejected after {attempts} attempt(s) (max {s.max_retries} retries). "
                           f"Last attempt left on branch {branch}; worktree kept at {worktree}.")
            logf.write(summary + "\n")
        self.db.finish_step(step, "succeeded" if accepted else "failed", summary=summary)
        self.db.update_task(
            task_id, status="accepted" if accepted else "failed", summary=summary, finished_at=now()
        )


def _load_review(path: Path, result_text: str) -> dict | None:
    candidates = []
    if path.exists():
        candidates.append(path.read_text(encoding="utf-8", errors="replace"))
    candidates += reversed(re.findall(r"```json\s*(\{.*?\})\s*```", result_text or "", re.S))
    for text in candidates:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("verdict") in ("accept", "retry"):
            return data
    return None
