"""POC ADK API server; Cloud Run IAM protects every route, including metadata."""
import hashlib
import os
from pathlib import Path
import platform

from fastapi.encoders import jsonable_encoder
from google.adk.cli.fast_api import get_fast_api_app
from vertexai._genai import types
from deployment.hosted.apps.load.agent import root_agent as load_agent
from deployment.hosted.apps.support.agent import root_agent as support_agent

ROOT = Path(__file__).resolve().parents[2]


def metadata():
    sources = {}
    for folder in ('examples/load', 'examples/support', 'deployment/hosted'):
        for path in sorted((ROOT / folder).rglob('*.py')):
            sources[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ('pyproject.toml', 'uv.lock', 'deployment/hosted/Dockerfile'):
        sources[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    return jsonable_encoder({'schema_version': 1,
        'agents': {'load': types.evals.AgentData.get_agents_map(load_agent),
                   'support': types.evals.AgentData.get_agents_map(support_agent)},
        'source_sha256': sources, 'deployment_revision': os.environ.get('K_REVISION', 'local'),
        'runtime': {'platform': platform.platform(), 'python': platform.python_version(),
                    'session_backend': 'in_memory', 'workers': 1}})


app = get_fast_api_app(agents_dir=str(Path(__file__).parent / 'apps'), web=False,
                       use_local_storage=False, reload_agents=False, auto_create_session=False)


@app.get('/eval/metadata')
def evaluation_metadata():
    return metadata()
