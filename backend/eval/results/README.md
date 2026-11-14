# Committed eval results

Baseline summaries live here as `baseline-<date>.md`. They are the only part of
a run that is committed: `../runs/` (raw per-name engine output) and `../cache/`
(the downloaded dataset, CC BY-SA 4.0) are gitignored and reproducible from the
provenance header each summary carries.

Every summary states the espeak-ng version, dataset revision, Kokoro vocabulary
revision and package versions it was measured against. Two summaries are only
comparable where those agree.
