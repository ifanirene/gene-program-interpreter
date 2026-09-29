import asyncio
from claude_agent_sdk import AssistantMessage, ToolUseBlock
from research import research_parallel as rp


def test_submission_survives_terminal_sdk_error(monkeypatch):
    payload={'program_id':'P1','candidate_mechanisms':[]}
    async def stream(**kwargs):
        yield AssistantMessage(content=[ToolUseBlock(id='s',name=f'mcp__{rp.SUBMIT_SERVER_NAME}__{rp.SUBMIT_TOOL_NAME}',input=payload)],model='test')
        raise RuntimeError('Reached maximum budget ($2)')
    monkeypatch.setattr(rp,'query',stream)
    holder={}
    trace,info=asyncio.run(rp._drive_once(prompt='test',options=None,submit_holder=holder,per_program_timeout=5))
    assert holder['payload']==payload
    assert 'maximum budget' in info['session_error']
    assert len(trace)==1
