"""Summarize real native results without inventing missing evaluations."""

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
metrics = [
    "general_quality",
    "text_quality",
    "instruction_following",
    "final_response_quality",
    "final_response_match",
    "final_response_reference_free",
    "tool_use_quality",
    "grounding",
    "hallucination",
    "safety",
    "multi_turn_task_success",
    "multi_turn_tool_use_quality",
    "multi_turn_trajectory_quality",
    "multi_turn_general_quality",
    "multi_turn_text_quality",
    "gecko_text2image",
    "gecko_text2video",
]
observed = {m: [] for m in metrics}
for p in sorted((ROOT / "artifacts/runs").glob("**/results_*.json")):
    d = json.loads(p.read_text())
    for summary in d.get("summary_metrics") or []:
        name = re.sub(r"_v\d+$", "", summary["metric_name"])
        if name in observed:
            observed[name].append((p, summary))
lines = [
    "# Implementation status",
    "",
    "Updated from native result files on 2026-10-21. A verified metric means a real non-null Google score exists; it does not mean every case passed the release policy.",
    "",
    "| Metric | Status | Evidence |",
    "| --- | --- | --- |",
]
for metric in metrics:
    rows = observed[metric]
    if any(s.get("num_cases_valid", 0) > 0 for _, s in rows):
        path, summary = next(
            (p, s) for p, s in reversed(rows) if s.get("num_cases_valid", 0) > 0
        )
        status = "verified"
        note = f"{summary['num_cases_valid']} valid case(s), {summary.get('num_cases_error', 0)} error(s) in latest successful computation: `{path.relative_to(ROOT)}`"
    elif rows:
        status = "failed"
        note = "Native evaluator attempted; no valid score. Inspect preserved errors."
    elif metric.startswith("gecko"):
        status = "unsupported"
        note = "No image/video output in this text/tool agent; not attempted."
    else:
        status = "blocked"
        note = "Awaiting compatible live execution; no score claimed."
    lines.append(f"| `{metric}` | **{status}** | {note} |")
lines += [
    "",
    "## Milestones",
    "",
    "- Pinned install and lockfile: complete.",
    "- Actual ADK execution and native `eval run`: complete; smoke reference match 1.0.",
    "- Seven simulated tools and deterministic authorization tests: complete.",
    "- Full-suite native runs, comparison, optional probes and passing/failing gates: see RESULTS.md for final evidence.",
    "- All original failed runs are retained in ignored `artifacts/runs/`; curated evidence stays local under ignored `artifacts/evidence/`, outside the source package.",
    "",
]
(ROOT / "docs/IMPLEMENTATION_STATUS.md").write_text("\n".join(lines))
