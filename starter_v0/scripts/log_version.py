from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIELDNAMES = [
    "version", "author", "changed_artifact", "artifact_version", "prompt_hash", "tools_hash",
    "reason", "hypothesis", "metric_name", "metric_before", "metric_after", "run_file",
]
HASH_COLUMNS = {"system_prompt.md": "prompt_hash", "tools.yaml": "tools_hash"}


def short_hash(value: str, length: int = 12) -> str:
    return (value or "")[:length]


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def relative_run_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def main() -> None:
    parser = argparse.ArgumentParser(description="Append a version_log.csv row from a run JSON produced by run_eval.py.")
    parser.add_argument("run", type=Path, help="Run JSON file, e.g. runs/v1_B_base_openrouter_<timestamp>.json")
    parser.add_argument("--author", required=True)
    parser.add_argument("--changed-artifact", required=True, help="baseline, system_prompt.md, tools.yaml, or system_prompt.md+tools.yaml")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--hypothesis", required=True)
    parser.add_argument("--metric", default="case_accuracy", help="Key in the run summary, e.g. case_accuracy or multiturn_accuracy.")
    parser.add_argument("--metric-before", default=None, help="Defaults to metric_after of the previous row with the same metric.")
    parser.add_argument("--log", type=Path, default=ROOT / "artifacts" / "version_log.csv")
    args = parser.parse_args()

    run: dict[str, Any] = json.loads(args.run.read_text(encoding="utf-8"))
    summary = run.get("summary", {})
    if summary.get("provider_error_cases") != 0 or summary.get("measured_cases") != summary.get("total_cases"):
        raise SystemExit(
            "ERROR: run is not valid evidence "
            f"(provider_error_cases={summary.get('provider_error_cases')}, "
            f"measured_cases={summary.get('measured_cases')}, total_cases={summary.get('total_cases')})"
        )
    if summary.get(args.metric) is None:
        raise SystemExit(f"ERROR: metric {args.metric!r} not found in run summary")

    rows = read_rows(args.log)
    version = run["version"]
    if any(row["version"] == version for row in rows):
        raise SystemExit(f"ERROR: {version} already exists in {args.log}; use a new version label")

    changed = [item.strip() for item in args.changed_artifact.split("+")]
    previous = rows[-1] if rows else None
    if previous:
        for artifact, column in HASH_COLUMNS.items():
            unchanged = previous[column] == short_hash(run[column])
            if artifact in changed and unchanged:
                raise SystemExit(
                    f"ERROR: {artifact} hash is unchanged since {previous['version']}; "
                    "this run does not reflect the claimed change"
                )
            if artifact not in changed and not unchanged:
                print(
                    f"WARNING: {artifact} also changed since {previous['version']} "
                    "but is not listed in --changed-artifact",
                    file=sys.stderr,
                )

    metric_before = args.metric_before
    if metric_before is None:
        same_metric = [row for row in rows if row["metric_name"] == args.metric]
        metric_before = same_metric[-1]["metric_after"] if same_metric else ""

    rows.append({
        "version": version,
        "author": args.author,
        "changed_artifact": args.changed_artifact,
        "artifact_version": run["artifact_version"],
        "prompt_hash": short_hash(run["prompt_hash"]),
        "tools_hash": short_hash(run["tools_hash"]),
        "reason": args.reason,
        "hypothesis": args.hypothesis,
        "metric_name": args.metric,
        "metric_before": metric_before,
        "metric_after": str(summary[args.metric]),
        "run_file": relative_run_path(args.run),
    })

    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Appended {version} ({run['artifact_version']}) to {args.log}")


if __name__ == "__main__":
    main()
