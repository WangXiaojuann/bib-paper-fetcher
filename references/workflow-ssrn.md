# SSRN Workflow

Use this workflow when the paper list already exists and the user wants direct SSRN-hosted PDF downloading.

## Before Running

Accepted input patterns:

- required: `title`
- recommended: `doi` or `ssrn_id`
- optional: `authors`, `year`, `journal`, `article_url`, `source_url`, `note`, and `formatted`

The fetcher can resolve SSRN records from:

- `ssrn_id`
- DOI values such as `10.2139/ssrn.1234567`
- `papers.ssrn.com` URLs
- notes or source URLs that contain an SSRN abstract page

Launch the shared Edge remote-debugging session and open the supported source pages before the batch starts:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\prepare_live_session_logins.ps1
```

## Manual Session Preparation

In the opened Edge window:

1. open SSRN in the same live session
2. complete any security verification shown by SSRN
3. open one representative SSRN abstract page
4. click the download button once if the site requires a manual consent or viewer setup
5. keep the Edge window open

## Run the Batch

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_ssrn_live_session_fetch.ps1 `
  -InputCsv .\ssrn_input.csv `
  -OutDir .\out\run-ssrn-001 `
  -PageWaitSeconds 8 `
  -InterItemSleepSeconds 3
```

If the same Edge session is already prepared:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_ssrn_live_session_fetch.ps1 `
  -InputCsv .\ssrn_input.csv `
  -OutDir .\out\run-ssrn-001 `
  -PrepareLogin $false
```

## How the Fetch Works

1. build or open the SSRN abstract page URL
2. detect security-verification pages without trying to bypass them
3. locate an SSRN download target, including `Delivery.cfm` links
4. fetch the PDF bytes inside the same browser session
5. validate that the response is a real PDF before saving it

## Outputs

The fetcher writes:

- `pdfs/`
- `ssrn_results.csv`
- `ssrn_missing.csv`
- `summary.txt`

## Retry Rules

- if a row returns `security_verification`, complete the verification in the same Edge session and retry only the failed SSRN subset
- if a row returns `no_download_link`, manually check whether the SSRN page exposes a PDF to the current session
- if a row returns `pdf_fetch_failed`, retry with a longer wait and confirm that the browser session can download the PDF manually
