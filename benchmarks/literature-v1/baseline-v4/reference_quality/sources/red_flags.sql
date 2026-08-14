-- DuckDB query for the citation-level audit table.
SELECT
  session_id,
  program_id,
  review_id,
  red_flag,
  support,
  declared_context,
  assessed_context,
  paper_title,
  rationale
FROM read_csv_auto('red_flags.csv', header = true)
ORDER BY session_id, review_id, red_flag;

