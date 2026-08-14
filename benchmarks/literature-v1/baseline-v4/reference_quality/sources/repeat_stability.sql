-- DuckDB query for repeat-run stability rows stored in the metrics artifact.
SELECT repeat_row.*
FROM read_json_auto('metrics.json'),
UNNEST(repeat_run_stability) AS repeat_row;

