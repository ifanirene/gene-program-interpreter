-- DuckDB query for per-program union and run-level supplied-gene coverage.
SELECT
  case_id,
  program_id,
  run_count,
  supplied_gene_count,
  cited_gene_count,
  citation_coverage,
  function_supported_gene_count,
  function_supported_coverage,
  mean_run_citation_coverage,
  uncovered_genes
FROM read_csv_auto('per_program.csv', header = true)
ORDER BY program_id;
