"""Investigate extra native metrics on real traces; never part of release gating."""

import argparse
import copy
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def run(args, log):
    with log.open("w") as out:
        p = subprocess.run(
            [str(ROOT / ".venv/bin/agents-cli"), "eval", *map(str, args)],
            cwd=ROOT,
            env={**os.environ, "CI": "1", "AGENTS_CLI_TELEMETRY": "0"},
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            timeout=600,
        )
    return {
        "command": ["agents-cli", "eval", *map(str, args)],
        "exit_code": p.returncode,
    }


def chat_projection(dataset):
    """Lossless text-only projection of actual messages; tool events remain in source."""
    cases = []
    for case in dataset["eval_cases"]:
        messages = []
        for turn in case["agent_data"]["turns"]:
            for event in turn["events"]:
                content = event.get("content") or {}
                parts = [
                    p
                    for p in content.get("parts", [])
                    if p.get("text") and not p.get("thought")
                ]
                if not parts:
                    continue
                messages.append(
                    {
                        "author": event["author"],
                        "content": {
                            "role": "user" if event["author"] == "user" else "model",
                            "parts": parts,
                        },
                    }
                )
        user_indices = [i for i, m in enumerate(messages) if m["author"] == "user"]
        split = user_indices[-1]
        cases.append(
            {
                "eval_case_id": case["eval_case_id"],
                "conversation_history": messages[:split],
                "prompt": messages[split]["content"],
                "responses": copy.deepcopy(case["responses"]),
            }
        )
    return {"eval_cases": cases}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("traces", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    commands = []
    metrics = "multi_turn_general_quality,multi_turn_text_quality"
    commands.append(
        run(
            [
                "grade",
                "--traces",
                args.traces,
                "--metrics",
                metrics,
                "--output",
                args.output / "native-incompatible",
                "--qps",
                "1",
            ],
            args.output / "native-incompatible.log",
        )
    )
    projection = args.output / "chat-projection.json"
    projection.write_text(
        json.dumps(chat_projection(json.loads(args.traces.read_text())), indent=2)
        + "\n"
    )
    commands.append(
        run(
            [
                "grade",
                "--traces",
                projection,
                "--metrics",
                metrics,
                "--output",
                args.output / "chat-grades",
                "--qps",
                "1",
            ],
            args.output / "chat-grades.log",
        )
    )
    (args.output / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
