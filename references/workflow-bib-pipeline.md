# BibTeX Pipeline Workflow

Use this workflow when the user starts from a `.bib` file and wants the downloadable article PDFs saved locally.

## Stage Order

1. Initialize the run root with `init-bib-run`.
2. Parse the BibTeX file with `import-bib`.
3. Import only `@article` entries into `master_catalog.csv`.
4. Record skipped non-article entries in `_runs/discovery/bib_skipped.csv`.
5. If rows are missing DOI or URL data, run `enrich-dois`.
6. Build platform queues with `build-queues`.
7. Run the supported platform downloaders against the generated queue files.
8. Ingest platform results with `ingest-results`.
9. Run `finalize-run` to write summary, failed-row, and PDF validation CSVs.

## Entry Rules

- include only BibTeX `@article` entries
- skip `@book`, `@techreport`, `@inproceedings`, `@misc`, and other non-article entry types
- keep skipped entries in `bib_skipped.csv` so the user can review what was excluded
- do not silently drop duplicate articles; let `master_catalog.csv` keep the canonical logical record

## Platform Order

After `build-queues`, use this default mixed-source batch order:

1. `jstor_input_known_stable.csv`
2. `wiley_input.csv`
3. `jstor_input_search.csv`
4. `ssrn_input.csv`
5. `sciencedirect_input.csv`

`build-queues` writes `recommended_download_order.txt` in the queue directory and keeps the backward-compatible all-in-one `jstor_input.csv`.

## Supported Routes

- ScienceDirect / Elsevier
- Wiley
- JSTOR
- SSRN

Rows outside supported routes should stay in `master_catalog.csv` with an unsupported or failed status instead of being removed.

## Canonical Outputs

The user-facing structured outputs are:

- `master_catalog.csv`
- `download_manifest.csv`
- `download_status_summary.csv`
- `failed_downloads.csv`
- `pdf_validation.csv`
- `papers/`

Everything else belongs under `_runs/`.

## Raw and Intermediate Files

BibTeX discovery files belong under `_runs/discovery/`:

- `_runs/discovery/bib_articles_raw.csv`
- `_runs/discovery/bib_skipped.csv`

Queue files belong under `_runs/queues/bib/`:

- `sciencedirect_input.csv`
- `wiley_input.csv`
- `jstor_input_known_stable.csv`
- `jstor_input_search.csv`
- `jstor_input.csv`
- `ssrn_input.csv`
- `unsupported.csv`
- `recommended_download_order.txt`

Platform run outputs belong under `_runs/platform/bib/<platform>/`.

## Download Discipline

- build queues from `master_catalog.csv`; do not hand-maintain queue files
- run PDF downloads serially by platform
- use the live Edge session for platforms that require browser access
- retry only failed subsets when possible
- ingest every platform result back into `master_catalog.csv` and `download_manifest.csv`
- run `finalize-run` after all platform result ingestion
- keep unsupported, inaccessible, and failed items recorded for auditability

## Final File Layout

Downloaded article PDFs go to:

- `papers/`

Use the canonical filename rule:

- `<AuthorSurnames>_<JournalAbbrev>_<PublicationYear>.pdf`
- SSRN-hosted downloads use `WP` as the journal abbreviation, regardless of BibTeX journal text

Example:

- `Kelly_Palhares_Pruitt_JF_2023.pdf`
- `Chen_Kelly_Xiu_WP_2022.pdf`
