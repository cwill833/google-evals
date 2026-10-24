"""Compatibility entry point for the original finance reference and preserved evidence."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adk_eval_harness.gate import read, digest, compact, comparable, text, observation_checks, write_report
from adk_eval_harness.gate import trace_checks as generic_checks, evaluate as generic_evaluate
from examples.finance.assertions import check


def trace_checks(case, rules):
    return generic_checks(case, rules) + check(case, rules)


def evaluate(run, policy_path):
    return generic_evaluate(run, policy_path, assertion_hook=check)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("run", type=Path)
    p.add_argument("--policy", type=Path, default=Path("tests/eval/policies/release.json"))
    args = p.parse_args()
    report = evaluate(args.run, args.policy)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
