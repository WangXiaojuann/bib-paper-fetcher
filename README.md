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

## How to Use

Add `bib-paper-fetcher` to your Codex skills, then ask Codex to download papers from a `.bib` file.

Example request:

> Use `bib-paper-fetcher` to download all available papers in `D:\cite.bib`.

Codex will use the live Edge session for logged-in access and write downloaded PDFs plus summary files locally.
