"""Physical source lines, including blanks/comments; no dependencies or generated evidence."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
core = ['run_evals.py', 'quality_gate.py', 'run_evals.sh']
groups = {
    'agent': list((ROOT / 'agent').glob('*.py')),
    'runner_and_gate': [ROOT / 'scripts' / name for name in core],
    'support_scripts': [p for p in (ROOT / 'scripts').glob('*.py') if p.name not in core],
    'unit_and_integration_tests': list((ROOT / 'tests').glob('test_*.py')),
    'controlled_fault_agent': list((ROOT / 'tests/fault_project').rglob('*.py')),
    'eval_data_config_policy': [p for p in (ROOT / 'tests/eval').rglob('*') if p.suffix in ['.json', '.yaml']],
}
report = {name: {'files': len(files), 'physical_lines': sum(len(p.read_text().splitlines()) for p in files),
                 'nonblank_lines': sum(sum(bool(s.strip()) for s in p.read_text().splitlines()) for p in files)}
          for name, files in groups.items()}
print(json.dumps(report, indent=2))
