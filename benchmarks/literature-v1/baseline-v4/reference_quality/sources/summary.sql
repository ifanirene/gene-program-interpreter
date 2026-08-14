-- DuckDB query for the report summary snapshot.
WITH metric_source AS (
  SELECT * FROM read_json_auto('metrics.json')
),
session_source AS (
  SELECT session_row.*
  FROM metric_source, UNNEST(per_session) AS session_row
)
SELECT
  aggregate.review_link_count AS review_link_count,
  aggregate.session_count AS session_count,
  aggregate.unique_paper_count AS unique_paper_count,
  (
    aggregate.support_distribution.supports
    + aggregate.support_distribution.partial
  )::DOUBLE / aggregate.review_link_count AS supportive_link_rate,
  (SELECT AVG(citation_coverage) FROM session_source) AS mean_citation_coverage,
  (
    SELECT AVG(function_supported_coverage)
    FROM session_source
  ) AS mean_function_supported_coverage,
  aggregate.context_overclaim_rate AS context_overclaim_rate,
  aggregate.contradiction_rate AS contradiction_rate,
  aggregate.red_flagged_link_count AS red_flagged_link_count,
  aggregate.red_flagged_link_rate AS red_flagged_link_rate,
  aggregate.red_flag_count AS red_flag_count
FROM metric_source;
