from unittest.mock import Mock

from gpi.gene_summaries import resolve_gene_summaries


def test_cached_descriptions_remap_to_new_programs_and_fetch_only_missing():
    client = Mock()
    client.get_gene_summaries.return_value = {"New": "new description"}
    result = resolve_gene_summaries(
        "harmonizome", {49: {"drivers": ["Known", "New"]}, 23: {"drivers": ["Known"]}},
        [49, 23], Mock(), client,
        cached_summaries={"Known": "saved description", "Unrelated": "ignore"},
    )
    client.get_gene_summaries.assert_called_once_with(["New"])
    assert result == {49: {"Known": "saved description", "New": "new description"},
                      23: {"Known": "saved description"}}


def test_complete_cache_requires_no_external_gene_lookup():
    client = Mock()
    result = resolve_gene_summaries(
        "harmonizome", {1: {"drivers": ["Known"]}}, [1], Mock(), client,
        cached_summaries={"Known": "saved description"},
    )
    client.get_gene_summaries.assert_not_called()
    assert result == {1: {"Known": "saved description"}}
