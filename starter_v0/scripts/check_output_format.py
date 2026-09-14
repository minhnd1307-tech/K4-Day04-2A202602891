from __future__ import annotations

import argparse
import json
from pathlib import Path


# Keep in sync with the "Output format" section of artifacts/system_prompt.md.
REQUIRED_KEYS = {"intent", "action", "reply", "evidence_ids"}
INTENTS = {
    "service_status", "device_diagnostics", "user_lookup", "kb_howto", "policy_question",
    "incident_report", "ticket", "device_public_info", "multi_source_triage", "capability",
    "cancellation", "out_of_scope", "security_refusal",
}
ACTIONS = {"answered", "refused", "cancelled", "awaiting_confirmation", "ticket_created", "tool_error"}


def check_answer(text: str) -> list[str]:
    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError as exc:
        return [f"not a bare JSON object ({exc.msg})"]
    if not isinstance(data, dict):
        return ["top-level value is not an object"]

    problems: list[str] = []
    if set(data) != REQUIRED_KEYS:
        problems.append(f"keys {sorted(data)} != {sorted(REQUIRED_KEYS)}")
    if data.get("intent") not in INTENTS:
        problems.append(f"unknown intent {data.get('intent')!r}")
    if data.get("action") not in ACTIONS:
        problems.append(f"unknown action {data.get('action')!r}")
    reply = data.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        problems.append("reply must be a non-empty string")
    evidence_ids = data.get("evidence_ids")
    if not isinstance(evidence_ids, list) or not all(isinstance(item, str) for item in evidence_ids):
        problems.append("evidence_ids must be an array of strings")
    return problems


def final_answers(path: Path) -> list[tuple[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    answers: list[tuple[str, str]] = []
    # run_eval.py output: only no-tool responses are final answers.
    for item in data.get("results", []):
        result = item["result"]
        if not result.get("actual_tool_calls") and result.get("actual_text"):
            answers.append((item["id"], result["actual_text"]))
    # chat.py transcript: clarify pauses and max-round stops are not final answers.
    for turn in data.get("turns", []):
        if turn.get("status") == "answered" and turn.get("assistant_text"):
            answers.append((f"turn {turn['turn_index']}", turn["assistant_text"]))
    return answers


def iter_json_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.glob("*.json")))
        else:
            files.append(path)
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description="Check final answers in run/transcript JSON against the system prompt output contract.")
    parser.add_argument("paths", nargs="+", type=Path, help="Run JSON, transcript JSON, or directories containing them.")
    args = parser.parse_args()

    checked = 0
    failed = 0
    for path in iter_json_files(args.paths):
        for label, text in final_answers(path):
            checked += 1
            problems = check_answer(text)
            if problems:
                failed += 1
                print(f"FAIL {path.name} {label}: {'; '.join(problems)}")

    print(f"format_compliance: {checked - failed}/{checked}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
