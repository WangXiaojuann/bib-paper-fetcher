# SSRN Troubleshooting

## Status Codes

### `security_verification`

Meaning:

- SSRN showed a security or bot-verification page before the paper page or PDF could be reached

What to do:

1. keep the same Edge window open
2. complete the verification manually
3. open the same SSRN abstract page once
4. rerun only the failed SSRN rows

### `no_article_url`

Meaning:

- the row did not contain enough information to build or discover an SSRN abstract page

What to do:

- add one of `ssrn_id`, `doi`, `article_url`, or `source_url`
- for DOI, prefer values like `10.2139/ssrn.1234567` when available

### `no_download_link`

Meaning:

- the abstract page loaded, but the fetcher did not find a usable SSRN PDF or `Delivery.cfm` target

Common causes:

- the page does not expose a public PDF
- the session still needs manual consent or verification
- the page layout changed

What to do:

- manually open the abstract page in the same Edge session
- click the available download button once
- retry the failed row

### `pdf_fetch_failed`

Meaning:

- a likely PDF target was found, but the final request did not return a valid PDF body

What to do:

- keep the same Edge session open
- increase `PageWaitSeconds`
- confirm that the browser can manually download the SSRN PDF
- rerun only the failed subset

## Session Problems

### The fetcher cannot attach to the session

Check:

- Edge was launched with `--remote-debugging-port`
- the port matches the command, usually `9222`
- the Edge window is still open

### SSRN repeatedly asks for verification

This workflow does not bypass verification. Finish it in the same live Edge window, avoid rapid repeated retries, and run a smaller failed subset after verification is complete.
