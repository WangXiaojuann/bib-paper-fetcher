---
name: bib-paper-fetcher
description: Download article PDFs from a BibTeX .bib file by parsing only @article entries, building a canonical catalog, routing supported papers to ScienceDirect / Elsevier, Wiley, JSTOR, or SSRN, and reusing the user's already authorized Microsoft Edge session. Use this skill whenever the user provides a .bib file or BibTeX references and wants the referenced article papers downloaded in batch. Skip @book, @techreport, and other non-article entry types.
---

# BibTeX Paper Fetcher

Use this skill when a `.bib` file is the source of truth and the agent should download official article PDFs through the user's already authorized Microsoft Edge window.

## Use This Skill For

- `.bib file -> @article entries -> master catalog -> platform queues -> official PDF downloads`
- batch downloading article references from BibTeX files
- direct platform downloads when the user already has queue CSVs for ScienceDirect / Elsevier, Wiley, JSTOR, or SSRN

Do not use this skill to create access the user does not already have.

## Core Rules

- parse and download only `@article` entries from the BibTeX file
- skip `@book`, `@techreport`, `@inbook`, `@manual`, and other non-article entry types
- for this version, do not specially filter article entries whose journal field mentions SSRN, arXiv, working paper, forthcoming, or similar labels; import them as `@article` rows and let route support determine whether they can be queued
- keep skipped non-article entries in `_runs/discovery/bib_skipped.csv`
- do not silently drop unsupported article entries; keep them in `master_catalog.csv` with `download_supported=false` and `download_status=unsupported_platform`
- when article rows lack DOI or official URLs, run DOI enrichment before building final platform queues
- download only official platform PDFs on supported routes; for SSRN, use the SSRN-hosted PDF exposed by the paper page
- keep all platform downloads strictly serial; do not parallelize PDF fetching or use sub-agents to bulk-download
- keep the live Edge session open during every download run
- if a previous run was interrupted, inspect the existing `out_dir` before resuming; for a clean restart, use a new `out_dir` or reinitialize explicitly
- retry only the failed subset unless the user explicitly asks for a fresh full rerun

## Canonical Outputs

The BibTeX workflow writes:

- `master_catalog.csv`
- `download_manifest.csv`
- `papers/`
- `_runs/discovery/bib_articles_raw.csv`
- `_runs/discovery/bib_skipped.csv`
- `_runs/queues/bib/`
- `_runs/platform/bib/`

`master_catalog.csv` is the logical master table. BibTeX article rows use:

- `record_type=bib_paper`

`download_manifest.csv` is the physical file manifest. Each row represents one PDF file that actually landed in `papers/`.

## BibTeX Workflow

### Stage 1: Initialize the Run Root

Run [scripts/run_bib_paper_pipeline.ps1](scripts/run_bib_paper_pipeline.ps1) with `init-bib-run`.

Required inputs:

- `bib_file`
- `out_dir`

Optional input:

- `collection_name`

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_bib_paper_pipeline.ps1 `
  init-bib-run `
  --bib-file <path-to-bib-file> `
  --out-dir <run-root> `
  --collection-name "my-paper-batch"
```

This creates the canonical output files and directories.

### Stage 2: Import Article Entries from the BibTeX File

Run `import-bib`.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_bib_paper_pipeline.ps1 `
  import-bib `
  --bib-file <path-to-bib-file> `
  --master-catalog <run-root>\master_catalog.csv
```

This command:

- parses only `@article` entries
- writes parsed article rows to `_runs/discovery/bib_articles_raw.csv`
- writes skipped non-article entries to `_runs/discovery/bib_skipped.csv`
- imports article rows into `master_catalog.csv` as `record_type=bib_paper`

Checkpoint before downloading:

- review `_runs/discovery/bib_skipped.csv` to confirm books and reports were skipped as intended
- review `master_catalog.csv` to see which article entries are routable and which are unsupported

### Stage 3: Enrich Missing DOIs When Needed

Many BibTeX files omit DOI fields. If routable article rows have empty `doi` and empty `article_url`, run `enrich-dois` before building final queues.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_bib_paper_pipeline.ps1 `
  enrich-dois `
  --master-catalog C:\path\to\run-root\master_catalog.csv
```

This uses Crossref to fill accepted DOI matches into `master_catalog.csv`. It accepts only high-similarity title matches and records lookup provenance in `notes`.

### Stage 4: Build Platform Queues

Run `build-queues`.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_bib_paper_pipeline.ps1 `
  build-queues `
  --master-catalog C:\path\to\run-root\master_catalog.csv `
  --out-dir C:\path\to\run-root\_runs\queues\bib
```

Supported routes:

- ScienceDirect / Elsevier
- Wiley
- JSTOR
- SSRN

Routing rule:

- if a row carries JSTOR-positive evidence such as `stable_id`, `stable_url`, a `jstor.org` article URL, or `jstor_status=confirmed_on_jstor`, route it to JSTOR first
- if a row carries SSRN evidence such as `10.2139/ssrn...`, a `papers.ssrn.com` article URL, or `SSRN` in journal/publisher metadata, route it to SSRN
- otherwise infer ScienceDirect / Elsevier from Elsevier metadata, ScienceDirect URLs, or `10.1016/` DOI prefixes
- otherwise infer Wiley from Wiley metadata, Wiley URLs, or `10.1111/` DOI prefixes
- otherwise mark the article as unsupported and keep it in `master_catalog.csv`

Default mixed-source batch order after `build-queues`:

1. `jstor_input_known_stable.csv`
2. `wiley_input.csv`
3. `jstor_input_search.csv`
4. `ssrn_input.csv`
5. `sciencedirect_input.csv`

Follow `_runs/queues/bib/recommended_download_order.txt`.

### Stage 5: Prepare the Live Edge Session

Use [scripts/launch_edge_live_session.ps1](scripts/launch_edge_live_session.ps1) to launch a dedicated Edge session with remote debugging.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\launch_edge_live_session.ps1
```

Before a batch, open the supported source pages in that same Edge session.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\prepare_live_session_logins.ps1
```

Let the user complete the manual setup in that Edge window:

- sign in to the source pages they need
- pass any bot verification or challenge page
- open a representative article
- click `View PDF`, `PDF`, or `Download` once
- keep the window open

### Stage 6: Run Platform Downloads

Run only queue files that exist.

ScienceDirect / Elsevier:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_sciencedirect_live_session_fetch.ps1 `
  -InputCsv C:\path\to\run-root\_runs\queues\bib\sciencedirect_input.csv `
  -OutDir C:\path\to\run-root\_runs\platform\bib\sciencedirect
```

Wiley:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_wiley_live_session_fetch.ps1 `
  -InputCsv C:\path\to\run-root\_runs\queues\bib\wiley_input.csv `
  -OutDir C:\path\to\run-root\_runs\platform\bib\wiley
```

If Wiley stops on an institutional chooser page, either let the user select the institution manually in the live Edge window and rerun, or pass a non-personal institution value explicitly:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_wiley_live_session_fetch.ps1 `
  -InputCsv C:\path\to\run-root\_runs\queues\bib\wiley_input.csv `
  -OutDir C:\path\to\run-root\_runs\platform\bib\wiley `
  -InstitutionName "<institution name>"
```

Use `-AutoSelectRecentInstitution $true` only when the user confirms the first recent institution shown in the live Edge window is the intended one.

JSTOR:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_jstor_live_session_fetch.ps1 `
  -InputCsv C:\path\to\run-root\_runs\queues\bib\jstor_input_known_stable.csv `
  -OutDir C:\path\to\run-root\_runs\platform\bib\jstor
```

SSRN:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_ssrn_live_session_fetch.ps1 `
  -InputCsv C:\path\to\run-root\_runs\queues\bib\ssrn_input.csv `
  -OutDir C:\path\to\run-root\_runs\platform\bib\ssrn
```

If both JSTOR queue files exist, run the known-stable queue first, then the search queue.

Platform notes:

- ScienceDirect already includes built-in `View PDF` / `View full text` fallback logic
- if a ScienceDirect page explicitly says the institution `does not subscribe to this content`, stop retrying that item in the current session
- on Wiley, `no_pdfdirect_link` can still be a slow reader-loading state; retry with a longer wait before declaring failure
- on Wiley, `institution_selection_required` means the user must choose an institution manually or rerun with `-InstitutionName`; do not hard-code institution names in the skill
- if JSTOR title search misses a known published item, allow a manual retry with explicit `stable_id` or `stable_url` in the input CSV rather than dropping the paper immediately
- SSRN can show a security verification page; if that happens, complete verification in the same Edge window and retry only `ssrn_missing.csv` or the failed SSRN subset

### Stage 7: Ingest Platform Results

After each platform run, ingest its results.

ScienceDirect:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_bib_paper_pipeline.ps1 `
  ingest-results `
  --master-catalog C:\path\to\run-root\master_catalog.csv `
  --download-manifest C:\path\to\run-root\download_manifest.csv `
  --platform sciencedirect `
  --results-csv C:\path\to\run-root\_runs\platform\bib\sciencedirect\devtools_results.csv
```

Wiley:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_bib_paper_pipeline.ps1 `
  ingest-results `
  --master-catalog C:\path\to\run-root\master_catalog.csv `
  --download-manifest C:\path\to\run-root\download_manifest.csv `
  --platform wiley `
  --results-csv C:\path\to\run-root\_runs\platform\bib\wiley\wiley_results.csv
```

JSTOR:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_bib_paper_pipeline.ps1 `
  ingest-results `
  --master-catalog C:\path\to\run-root\master_catalog.csv `
  --download-manifest C:\path\to\run-root\download_manifest.csv `
  --platform jstor `
  --results-csv C:\path\to\run-root\_runs\platform\bib\jstor\jstor_results.csv
```

SSRN:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_bib_paper_pipeline.ps1 `
  ingest-results `
  --master-catalog C:\path\to\run-root\master_catalog.csv `
  --download-manifest C:\path\to\run-root\download_manifest.csv `
  --platform ssrn `
  --results-csv C:\path\to\run-root\_runs\platform\bib\ssrn\ssrn_results.csv
```

### Stage 8: Finalize and Validate the Run

After all available platform results have been ingested, run `finalize-run`.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_bib_paper_pipeline.ps1 `
  finalize-run `
  --master-catalog C:\path\to\run-root\master_catalog.csv `
  --download-manifest C:\path\to\run-root\download_manifest.csv
```

This writes:

- `download_status_summary.csv`
- `failed_downloads.csv`
- `pdf_validation.csv`

Final article PDFs must end up in:

- `papers/`

Filename rule:

- `<AuthorSurnames>_<JournalAbbrev>_<PublicationYear>.pdf`
- for SSRN-hosted downloads, always use `WP` as the journal abbreviation in the final filename

Example:

- `Kelly_Palhares_Pruitt_JF_2023.pdf`
- `Chen_Kelly_Xiu_WP_2022.pdf`

## Direct Platform Downloads

Use this path when discovery is already done and the user only wants platform-specific downloading.

For ScienceDirect or Wiley:

- required columns: `number`, `doi`
- optional columns: `title`, `note`, `year`, `journal`, `formatted`

For JSTOR:

- required column: `title`
- recommended column: `authors`
- optional columns: `ref_no`, `number`, `stable_id`, `stable_url`, `jstor_status`

For SSRN:

- required column: `title`
- recommended columns: `doi` or `ssrn_id`
- optional columns: `authors`, `year`, `journal`, `article_url`, `source_url`, `note`, `formatted`

Use the same live Edge setup and platform wrappers described above.

## References

- Read [references/workflow-bib-pipeline.md](references/workflow-bib-pipeline.md) for the full BibTeX batch-download workflow.
- Read [references/workflow.md](references/workflow.md) for the direct ScienceDirect / Elsevier run order.
- Read [references/troubleshooting-sciencedirect.md](references/troubleshooting-sciencedirect.md) for ScienceDirect troubleshooting.
- Read [references/workflow-wiley.md](references/workflow-wiley.md) for the Wiley run order.
- Read [references/troubleshooting-wiley.md](references/troubleshooting-wiley.md) for Wiley troubleshooting.
- Read [references/workflow-jstor.md](references/workflow-jstor.md) for the JSTOR run order.
- Read [references/troubleshooting-jstor.md](references/troubleshooting-jstor.md) for JSTOR troubleshooting.
- Read [references/workflow-ssrn.md](references/workflow-ssrn.md) for the SSRN run order.
- Read [references/troubleshooting-ssrn.md](references/troubleshooting-ssrn.md) for SSRN troubleshooting.
