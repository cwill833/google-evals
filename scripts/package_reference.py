"""Build a public reference zip from an explicit allowlist, never the workspace wholesale."""

import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "README.md",
    "LICENSE",
    ".gitignore",
    ".env.example",
    ".python-version",
    "pyproject.toml",
    "uv.lock",
    "agents-cli-manifest.yaml",
    "docs/agent_eval_architecture_v2.docx",
    "docs/ARCHITECTURE.md",
    "docs/AGENT_HANDOFF.md",
    "docs/RESEARCH.md",
    "docs/GOOGLE_UI_RESEARCH.md",
    "docs/research/google-ui-2026-10-24/evidence.json",
    "docs/research/google-ui-2026-10-24/saved-traces.json",
    "docs/research/google-ui-2026-10-24/original-native-grades.json",
    "docs/METRIC_INVENTORY.md",
    "docs/IMPLEMENTATION_STATUS.md",
    "docs/RESULTS.md",
    "docs/HARDENING.md",
    "docs/CODE_SIZE.md",
    "docs/STATEFUL_PASS.md",
    "docs/REUSABLE_ADK.md",
    "docs/REVIEW_AND_LOAD.md",
    "docs/QUEUE_AND_RECOVERY.md",
    "docs/HOSTED_AND_REPORTS.md",
    "docs/CONCURRENCY.md",
    "docs/CALIBRATION.md",
    "docs/UPSTREAM_ISSUES.md",
]
FOLDERS = ["agent", "adk_eval_harness", "examples", "packs", "scripts", "tests", "deployment/hosted"]


def publication_path(path):
    """Reject links, including directory ancestors, before traversing or reading."""
    relative = path.relative_to(ROOT)
    current = ROOT
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Symlink in publication allowlist: ' + str(relative))
    if not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('Publication path escapes root: ' + str(relative))
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/google-agent-evals-demo.zip")
    )
    args = parser.parse_args()
    files = [ROOT / p for p in FILES]
    for folder in FOLDERS:
        publication_path(ROOT / folder)
        for p in (ROOT / folder).rglob("*"):
            if any(
                x.startswith(".") or x == "__pycache__"
                for x in p.relative_to(ROOT / folder).parts
            ):
                continue
            publication_path(p)
            if p.is_file():
                files.append(p)
    for p in files:
        publication_path(p)
        if not p.exists():
            raise ValueError(f"Missing required publication file: {p}")
        if p.suffix == ".pyc" or p.name == ".env":
            raise ValueError("Private file in publication allowlist")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(set(files)):
            z.write(p, "google-agent-evals-demo/" + str(p.relative_to(ROOT)))
    print(f"Packaged {len(set(files))} files: {args.output}")


if __name__ == "__main__":
    main()
