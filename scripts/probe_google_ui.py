"""Opt-in research probe for Google UI publication; never imports an ADK agent.

Not part of the harness or release gate. See docs/GOOGLE_UI_RESEARCH.md.
Uses the pinned CLI's SDK compatibility imports, which are not a stable public API.
"""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["publish", "submit", "poll"])
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", default="us-central1")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--allow-cloud-actions", action="store_true")
    parser.add_argument("--grades", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--dest", help="GCS destination for cloud grading")
    parser.add_argument("--experiment", help="New experiment display/name")
    parser.add_argument("--run-name", default="existing-native-scores")
    parser.add_argument("--run", type=Path, help="Saved SDK run JSON, for poll/reuse")
    parser.add_argument("--candidate-model", help="Optional explicit matching Candidate 1 model")
    args = parser.parse_args()
    if not args.allow_cloud_actions:
        parser.error("Cloud access is opt-in: pass --allow-cloud-actions")
    if args.action == "publish" and not (args.grades and args.experiment):
        parser.error("publish requires --grades and a fresh --experiment")
    if args.action == "submit" and not (args.dest and (args.dataset or args.run)):
        parser.error("submit requires --dest and exactly one of --dataset or --run")
    if args.action == "submit" and bool(args.dataset) == bool(args.run):
        parser.error("submit requires exactly one of --dataset or --run")
    if args.action == "submit" and not args.run and not args.experiment:
        parser.error("new submit requires --experiment")
    if args.action == "poll" and not args.run:
        parser.error("poll requires --run")
    # Reserve a fresh output before any cloud writes; preserve failures separately.
    args.output.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json", exclude_none=True)
        with (args.output / name).open("x") as f:
            json.dump(value, f, indent=2, ensure_ascii=False)
            f.write("\n")

    try:
        if args.action == "publish":
            from google.cloud import aiplatform

            native = json.loads(args.grades.read_text())
            if len(native["summary_metrics"]) != 1:
                raise ValueError("This bounded probe requires exactly one native metric")
            summary = native["summary_metrics"][0]
            metric = summary["metric_name"]
            # This probe supports the complete two-case evidence, not failure recovery.
            if summary["num_cases_error"] or summary["num_cases_valid"] != len(native["eval_case_results"]):
                raise ValueError("Use a complete grade file; do not omit failed cases")
            metrics = {"native_mean_score": summary["mean_score"],
                       "native_pass_rate": summary["pass_rate"],
                       "native_valid_cases": summary["num_cases_valid"],
                       "native_error_cases": summary["num_cases_error"]}
            for case in native["eval_case_results"]:
                candidates = case["response_candidate_results"]
                if len(candidates) != 1:
                    raise ValueError("This bounded probe supports one candidate")
                result = candidates[0]["metric_results"][metric]
                if result.get("error_message") or result["score"] is None:
                    raise ValueError("Cannot publish an unavailable grade as a score")
                metrics[f"case_{case['eval_case_index']}_native_score"] = result["score"]
            aiplatform.init(project=args.project, location=args.region,
                            experiment=args.experiment, experiment_tensorboard=False)
            with aiplatform.start_run(args.run_name):
                aiplatform.log_params({
                    "native_metric": metric,
                    "source_sha256": hashlib.sha256(args.grades.read_bytes()).hexdigest(),
                    "source_file": args.grades.name,
                    "publication_mode": "existing Google scores; no regrading",
                })
                aiplatform.log_metrics(metrics)
            save("readback.json", aiplatform.get_experiment_df().to_dict(orient="records"))
            return

        from google.agents.cli._agent_platform import AgentPlatformClient
        from google.agents.cli._agent_platform_types import types

        client = AgentPlatformClient(project=args.project, location=args.region)
        previous = json.loads(args.run.read_text()) if args.run else None
        if args.action == "poll":
            run = client.evals.get_evaluation_run(name=previous["name"])
            save("readback.json", run)
            print(run.name, run.state)
            return
        if previous:
            # Reuse the exact completed saved responses and experiment from a prior submit.
            dataset = types.EvaluationRunDataSource(**previous["data_source"])
            experiment = json.loads((args.run.parent / "experiment.json").read_text())["name"]
        else:
            dataset = types.EvaluationDataset.model_validate_json(args.dataset.read_text())
            experiment_obj = client.evals.create_evaluation_experiment(display_name=args.experiment)
            experiment = experiment_obj.name
            save("experiment.json", experiment_obj)
        if previous:
            save("experiment.json", {"name": experiment})
        extra = {}
        if args.candidate_model:
            extra = {
                "inference_configs": {"Candidate 1": types.EvaluationRunInferenceConfig(model=args.candidate_model)},
                "config": {"allow_cross_region_model": True},
            }
        run = client.evals.create_evaluation_run(
            dataset=dataset, dest=args.dest,
            metrics=[types.Metric(name="final_response_match_v2")],
            evaluation_experiment=experiment,
            display_name="Saved responses UI publication probe", **extra,
        )
        save("run.json", run)
        print(run.name, run.state)
    except Exception as exc:
        save("error.json", {"type": type(exc).__name__, "message": str(exc)})
        raise


if __name__ == "__main__":
    main()
