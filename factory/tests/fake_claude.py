"""Stand-in for the `claude` CLI to dry-run the factory loop without an LLM.

Usage: --claude-cmd "python <path>/fake_claude.py"

The builder commits a dummy file, runs the checks command and writes an
artifact. The reviewer asks for a retry on the first attempt and accepts the
second, so one run exercises the whole retry path.
"""

import json
import re
import subprocess
import sys
from pathlib import Path


def emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def say(text: str) -> None:
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})


def tool(name: str, command: str, output: str, ok: bool = True) -> None:
    tid = f"tool_{abs(hash(command)) % 10**8}"
    emit({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": name, "input": {"command": command}}]}})
    emit({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": not ok, "content": output}]}})


def sh(command: str) -> tuple[int, str]:
    proc = subprocess.run(command, shell=True, capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    tool("Bash", command, out, proc.returncode == 0)
    return proc.returncode, out


def main() -> int:
    args = sys.argv[1:]
    system = Path(args[args.index("--append-system-prompt-file") + 1]).read_text(encoding="utf-8")
    prompt = sys.stdin.read()
    emit({"type": "system", "subtype": "init", "model": "fake", "cwd": str(Path.cwd())})

    attempt = int(re.search(r"Attempt(?: under review)?: \*\*(\d+) of", prompt).group(1))
    checks_cmd = re.search(r"```\n\s*(python .+?)\n\s*```", prompt).group(1).strip()

    if "# Role: Builder" in system:
        attempt_dir = Path(re.search(r"write your artifact here\): `(.+?)`", prompt).group(1))
        say(f"Working on attempt {attempt}.")
        Path(f"factory_dry_run_{attempt}.txt").write_text(f"attempt {attempt}\n")
        sh("git add -A")
        sh(f'git commit -q -m "test: dry run attempt {attempt}"')
        code, _ = sh(checks_cmd)
        (attempt_dir / "artifact.md").write_text(
            f"# Artifact: dry run\n\n## Status\n{'PASS' if code == 0 else 'FAIL'}\n\n## Summary\n"
            f"Fake builder attempt {attempt}.\n", encoding="utf-8")
        result = f"Committed a dummy file and ran checks (exit {code})."
    else:
        review_dir = Path(re.search(r"Review directory .*?: `(.+?)`", prompt).group(1))
        code, _ = sh(checks_cmd)
        if attempt == 1:
            review = {"verdict": "retry", "tests_passing": code == 0, "summary": "First attempt: asking for a retry.",
                      "issues": [{"severity": "blocker", "description": "dry-run forced retry"}],
                      "feedback": "Dry run: please try again."}
        else:
            review = {"verdict": "accept", "tests_passing": code == 0, "summary": "Checks pass; accepted.",
                      "requirements": [{"requirement": "Add a dummy file", "status": "met",
                                        "evidence": f"factory_dry_run_{attempt}.txt committed"}],
                      "issues": [], "feedback": ""}
        (review_dir / "review.json").write_text(json.dumps(review, indent=2), encoding="utf-8")
        result = f"Verdict: {review['verdict']}\n\n```json\n{json.dumps(review)}\n```"

    emit({"type": "result", "subtype": "success", "is_error": False, "result": result,
          "total_cost_usd": 0.0, "num_turns": 3, "duration_ms": 1000})
    return 0


if __name__ == "__main__":
    sys.exit(main())
