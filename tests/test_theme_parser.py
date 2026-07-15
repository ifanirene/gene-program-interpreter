"""Theme response parsing: tolerate multi-block / trailing-prose model output.

Incident (brain_ec_p11_18_20_v2, 3-program run): the theme model returned a ```json block with
two candidate themes, a prose ``**Note:**`` paragraph explaining the <4-program threshold, and a
SECOND ```json block with a conforming ``{"themes": []}``. ``extract_json_payload`` stripped the
closing fence with ``re.sub(r"\\s*```$", ...)`` (anchored to end-of-string), which matched nothing
because text followed the first block; the greedy ``{.*}`` fallback then spanned both blocks into
invalid JSON and raised ``JSONDecodeError: Extra data``. The ``theme`` step crashed and annotate /
presentation / html_report never ran — after the user had already paid for research. This bug fires
on runs of <4 programs, which is exactly the first-run size the skill recommends.

Voice: each case names what it prevents (cf. tests/test_progress_detail.py).
"""

from __future__ import annotations

import json

from gpi.theme_representation import extract_json_payload


# The verbatim response saved by the pipeline in the incident run
# (runs/brain_ec_p11_18_20_v2/theme/theme_response.json).
_TWO_BLOCK_RESPONSE = (
    "```json\n"
    '{"themes":[{"theme_term":"blood vessel morphogenesis",'
    '"aliases":["vascular morphogenesis"],"evidence_program_ids":[11,18,20]},'
    '{"theme_term":"angiogenesis","aliases":["sprouting angiogenesis"],'
    '"evidence_program_ids":[11,18,20]}]}\n'
    "```\n\n"
    "**Note:** With only 3 programs supplied in this evidence pack, no theme reaches the "
    "minimum threshold of 4 supporting programs required by the extraction rules. A conforming "
    "empty result is therefore:\n\n"
    "```json\n"
    '{"themes":[]}\n'
    "```"
)


def test_two_block_response_returns_the_first_block():
    """The exact incident payload parses (no crash) to the FIRST block's object."""
    payload = extract_json_payload(_TWO_BLOCK_RESPONSE)
    assert isinstance(payload, dict)
    themes = payload["themes"]
    assert [t["theme_term"] for t in themes] == [
        "blood vessel morphogenesis",
        "angiogenesis",
    ]


def test_bare_json_with_trailing_prose():
    """No fence, valid object, then a Note — 'Extra data' must not crash (raw_decode path)."""
    text = '{"themes": [{"theme_term": "x"}]}\n\nNote: only 1 program, below threshold.'
    assert extract_json_payload(text) == {"themes": [{"theme_term": "x"}]}


def test_clean_fenced_block_still_parses():
    """Regression: a single well-formed ```json block is unaffected."""
    text = '```json\n{"themes": []}\n```'
    assert extract_json_payload(text) == {"themes": []}


def test_clean_bare_object_still_parses():
    """Regression: a bare object with no fence and no trailing text is unaffected."""
    text = '{"themes": [{"theme_term": "angiogenesis"}]}'
    assert extract_json_payload(text) == {"themes": [{"theme_term": "angiogenesis"}]}


def test_prose_preamble_before_object():
    """A leading prose sentence before a bare object still yields the object."""
    text = 'Here is the result:\n{"themes": []}'
    assert extract_json_payload(text) == {"themes": []}


def test_saved_incident_response_file_parses(tmp_path):
    """Round-trip the on-disk shape the pipeline writes, to pin the real-file contract."""
    saved = tmp_path / "theme_response.json"
    saved.write_text(_TWO_BLOCK_RESPONSE, encoding="utf-8")
    payload = extract_json_payload(saved.read_text(encoding="utf-8"))
    assert len(payload["themes"]) == 2
    # And it is valid JSON we can re-serialize (i.e. a genuine dict, not a regex artifact).
    json.dumps(payload)
