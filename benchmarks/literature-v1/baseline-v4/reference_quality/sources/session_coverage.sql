-- DuckDB query for the per-session chart and exact-value table.
SELECT
  session_id,
  case_id,
  program_id,
  supplied_gene_count,
  cited_gene_count,
  citation_coverage,
  function_supported_gene_count,
  function_supported_coverage,
  review_link_count,
  unique_citation_count,
  context_overclaim_rate,
  red_flagged_link_count,
  red_flagged_link_rate,
  red_flag_count
FROM read_csv_auto('per_session.csv', header = true)
ORDER BY session_id;
