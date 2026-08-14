-- DuckDB query for double-score agreement and adjudication provenance.
SELECT
  double_scored_count,
  double_scored_fraction,
  exact_agreement_count,
  exact_agreement_rate,
  disagreement_count,
  adjudicated_count,
  field_agreement_rates
FROM read_json_auto('reliability.json');

