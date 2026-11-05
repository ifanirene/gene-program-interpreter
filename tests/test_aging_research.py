import asyncio
import json
import httpx
from claude_agent_sdk import AssistantMessage, ToolUseBlock, ResultMessage
from research import research_parallel as rp
from research.schema import AgentResearchResult
from research.verify import normalize_agent_result
from research.literature import LiteratureClient


def test_custom_protocol_is_used_without_resuming_another_program(tmp_path):
    protocol = tmp_path / "protocol.md"
    protocol.write_text("aging-specific task")
    bundle = tmp_path / "P8.json"
    bundle.write_text('{"program_id":"P8"}')
    summary = rp.dry_run([bundle], out_dir=tmp_path / "out", protocol_path=protocol,
                         model="claude-opus-5-5", max_budget_usd=5, max_turns=90)
    assert summary["per_program"][0]["model"] == "claude-opus-5-5"
    assert summary["per_program"][0]["max_budget_usd"] == 5
    options = rp.build_options(workspace=tmp_path, system_prompt=rp.read_protocol(protocol),
                               submit_holder={}, model="claude-opus-5-5", max_turns=90, max_budget_usd=5)
    assert options.system_prompt == "aging-specific task"
    assert not options.resume and not options.continue_conversation


def test_aging_decisions_survive_normalization():
    aging = dict(summary="test", direction_interpretation="association", display_modules=["a"],
                 mechanism_priority="pursue", mechanism_reason="mechanism gap",
                 benefit_priority="unresolved", benefit_reason="no aged intervention",
                 potential_action="unknown", driver_status="unresolved", functional_question="why?",
                 distinguishing_test="measure activity", key_limit="association")
    raw = dict(program_id="P8", aging_annotation=aging,
               key_gene_evidence=[dict(gene="X", summary="single-gene lead", papers=[])])
    result = normalize_agent_result(AgentResearchResult.model_validate(raw))
    assert result.aging_annotation.model_dump() == aging
    assert result.key_gene_evidence == raw["key_gene_evidence"]


def test_session_identity_and_returned_model_are_archived(tmp_path, monkeypatch):
    async def stream(**kwargs):
        yield AssistantMessage(content=[ToolUseBlock(id="s", name="mcp__gpi__submit_result",
                    input={"program_id":"P8"})], model="claude-opus-5-5")
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                            is_error=False, num_turns=1, session_id="independent-session")
    monkeypatch.setattr(rp, "query", stream)
    path = tmp_path / "messages.jsonl"
    trace, result = asyncio.run(rp._drive_once(prompt="test", options=None,
                submit_holder={}, per_program_timeout=5, transcript_path=path))
    assert result["session_id"] == "independent-session"
    assert result["assistant_models"] == ["claude-opus-5-5"]
    assert len([json.loads(line) for line in path.read_text().splitlines()]) == 2


def test_fulltext_retrieval_is_read_only_and_preserves_sections():
    def handler(request):
        assert request.method == "GET"
        assert request.url.host == "www.ebi.ac.uk"
        return httpx.Response(200, text="<article><body><sec><title>Results</title><p>Old mice improved.</p></sec></body></article>")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = LiteratureClient(http_client=http)
            data = await client.fetch_pmc_fulltext("PMC123")
            assert data["sections"] == [{"title":"Results", "text":"Old mice improved."}]
            try:
                await client.fetch_pmc_fulltext("https://untrusted.example/")
            except ValueError:
                pass
            else:
                raise AssertionError("Full text must accept only PMC IDs")
    asyncio.run(run())
