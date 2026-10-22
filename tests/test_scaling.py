"""Platform scaling/lifecycle regressions, independent of model answer quality."""
import asyncio
import json
from pathlib import Path
import sys
import pytest
from google.adk.agents import BaseAgent, LlmAgent
from google.adk.events import Event
from google.genai import types
from adk_eval_harness.generation import generate
from adk_eval_harness import runner


class StatefulObjectAgent(BaseAgent):
    calls: int = 0
    async def _run_async_impl(self, ctx):
        self.calls += 1
        yield Event(author=self.name, content=types.Content(role='model',parts=[types.Part(text=str(self.calls))]))


def test_factory_creates_agent_per_case_but_retains_it_between_turns(tmp_path):
    creations=[]
    def factory():
        agent=StatefulObjectAgent(name='object_counter');creations.append(agent);return agent
    out=tmp_path/'trace.json'
    asyncio.run(generate([{'eval_case_id':'case-one','messages':['one','two']},
                          {'eval_case_id':'case-two','messages':['one']}],out,None,agent_factory=factory))
    assert len(creations)==2
    traces=json.loads(out.read_text())['eval_cases']
    assert [c['responses'][0]['response']['parts'][0]['text'] for c in traces]==['2','1']
    assert [json.loads(line) for line in (tmp_path/'completed_cases.jsonl').read_text().splitlines()]==traces
    assert json.loads((tmp_path/'progress.json').read_text())['complete']


def test_unsupported_metadata_fails_before_any_runner_or_tool_execution(tmp_path):
    calls=[]
    def should_not_run(**kwargs): calls.append(kwargs);raise AssertionError('must not execute')
    agent=LlmAgent(name='dynamic',instruction=lambda ctx:'computed')
    with pytest.raises(ValueError,match='metadata is unsupported'):
        asyncio.run(generate([{'eval_case_id':'one','messages':['hello']}],tmp_path/'trace.json',agent,
                             runner_factory=should_not_run))
    assert not calls


def test_failed_case_preserves_linear_checkpoint_without_final_trace(tmp_path):
    from examples.load.agent import create_agent
    good=json.dumps({'case_id':'one','payload':'ok'})
    fail=json.dumps({'case_id':'two','payload':'bad','fail':True})
    out=tmp_path/'trace.json'
    with pytest.raises(RuntimeError,match='Injected'):
        asyncio.run(generate([{'eval_case_id':'one','messages':[good]},
                              {'eval_case_id':'two','messages':[fail]}],out,None,agent_factory=create_agent))
    assert not out.exists()
    journal=(tmp_path/'completed_cases.jsonl').read_text().splitlines()
    assert len(journal)==1 and json.loads(journal[0])['eval_case_id']=='one'
    assert json.loads((tmp_path/'progress.json').read_text())['complete'] is False
    assert (tmp_path/'sessions/two/turn_0.json').exists()
    with pytest.raises(ValueError,match='must be new'):
        asyncio.run(generate([{'eval_case_id':'one','messages':[good]}],out,None,agent_factory=create_agent))


def test_check_rejects_evidence_from_another_config(tmp_path,monkeypatch,capsys):
    root=Path(__file__).resolve().parents[1]
    from tests.fixture_runs import make_run
    run = make_run(tmp_path)
    monkeypatch.setattr(sys,'argv',['harness','check','--config',str(root/'packs/support/config.json'),
                                  '--output',str(run)])
    assert runner.main()==1
    assert 'do not match' in capsys.readouterr().out


def test_invalid_pack_never_starts_native_commands(tmp_path, monkeypatch):
    from adk_eval_harness.config import load
    root=Path(__file__).resolve().parents[1]
    config=load(root/'packs/support/config.json')
    config['suites']={}
    def unwanted(*a,**k): raise AssertionError('native command ran before validation')
    monkeypatch.setattr(runner,'command',unwanted)
    with pytest.raises(ValueError): runner.run(config,tmp_path/'run')
    assert not (tmp_path/'run').exists()


def test_failure_in_first_case_cannot_overwrite_partial_evidence(tmp_path):
    from examples.load.agent import create_agent
    cases=[{'eval_case_id':'one','messages':[json.dumps({'case_id':'one','payload':'x','fail':True})]}]
    out=tmp_path/'trace.json'
    with pytest.raises(RuntimeError,match='Injected'):
        asyncio.run(generate(cases,out,None,agent_factory=create_agent))
    partial=(tmp_path/'sessions/one/turn_0.json').read_bytes()
    with pytest.raises(ValueError,match='must be new'):
        asyncio.run(generate(cases,out,None,agent_factory=create_agent))
    assert (tmp_path/'sessions/one/turn_0.json').read_bytes()==partial
