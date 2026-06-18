# bib-paper-fetcher

`bib-paper-fetcher` is a Codex skill for downloading paper PDFs from a BibTeX `.bib` file.

Given a `.bib` file, the skill:

- parses BibTeX entries
- keeps only `@article` records
- skips books, reports, proceedings, and other non-article entries
- enriches missing DOIs when needed
- routes supported papers to publisher/platform queues
- downloads available PDFs through the user's logged-in Microsoft Edge session
- saves final PDFs and run summaries locally

## Supported Platforms

Current download routes:

- ScienceDirect / Elsevier
- Wiley Online Library
- JSTOR
- SSRN

The skill does not create access. It only reuses access that the user already has in the live Edge session.

## Output

The main outputs are:

- `papers/`: downloaded PDFs
- `master_catalog.csv`: parsed and routed paper catalog
- `download_manifest.csv`: downloaded file manifest
- `download_status_summary.csv`: final status counts
- `failed_downloads.csv`: papers not downloaded
- `pdf_validation.csv`: local PDF existence/signature check

SSRN-hosted papers use `WP` as the journal abbreviation in final PDF filenames.

## How to Use

Install or place this folder as a Codex skill. Then ask Codex to download the papers from a `.bib` file and provide the file path.

Example request:

> Use `bib-paper-fetcher` to download all available papers in `D:\cite.bib`.

Codex will read `SKILL.md`, run the bundled scripts as needed, use the live Edge session for logged-in access, and place downloaded PDFs plus summary files in the chosen output folder.

The scripts under `scripts/` are implementation resources for the skill. Users normally do not need to run them manually.
