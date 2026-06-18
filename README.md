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

## Basic Use

Run the BibTeX pipeline with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_bib_paper_pipeline.ps1 init-bib-run --bib-file <path-to-bib-file> --out-dir <run-root>
powershell -ExecutionPolicy Bypass -File .\scripts\run_bib_paper_pipeline.ps1 import-bib --bib-file <path-to-bib-file> --master-catalog <run-root>\master_catalog.csv
powershell -ExecutionPolicy Bypass -File .\scripts\run_bib_paper_pipeline.ps1 enrich-dois --master-catalog <run-root>\master_catalog.csv
powershell -ExecutionPolicy Bypass -File .\scripts\run_bib_paper_pipeline.ps1 build-queues --master-catalog <run-root>\master_catalog.csv --out-dir <run-root>\_runs\queues\bib
```

Then run the platform wrapper scripts for the queue files that exist, ingest their result CSVs, and finish with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_bib_paper_pipeline.ps1 finalize-run --master-catalog <run-root>\master_catalog.csv --download-manifest <run-root>\download_manifest.csv
```
