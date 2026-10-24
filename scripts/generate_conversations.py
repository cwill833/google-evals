"""Compatibility runner for the original finance example."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adk_eval_harness.generation import generate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    from agent.agent import root_agent
    asyncio.run(generate(json.loads(args.dataset.read_text()), args.output, root_agent, user_id='C001'))


if __name__ == '__main__':
    main()
