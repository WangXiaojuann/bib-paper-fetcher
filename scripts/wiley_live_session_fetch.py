#!/usr/bin/env python3
"""Fetch official Wiley PDFs through a live logged-in Edge DevTools session."""

from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import time
from pathlib import Path
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

import websocket


URL_RE = re.compile(r"https?://[^\s;,\)]+", flags=re.I)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch official Wiley PDFs through a live Edge DevTools session.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input-csv", required=True, help="Input CSV with Wiley paper rows")
    parser.add_argument("--out-dir", required=True, help="Output directory for raw run artifacts")
    parser.add_argument("--debug-port", type=int, default=9222, help="Edge remote-debugging port")
    parser.add_argument("--page-wait-seconds", type=int, default=10, help="Seconds to wait after opening each tab")
    parser.add_argument("--inter-item-sleep-seconds", type=int, default=5, help="Seconds to sleep between rows")
    parser.add_argument(
        "--viewer-ws-timeout-seconds",
        type=int,
        default=180,
        help="Seconds to keep waiting for the Wiley PDF target",
    )
    parser.add_argument(
        "--institution-name",
        default="",
        help="Optional institution name to select on Wiley institutional login pages.",
    )
    parser.add_argument(
        "--auto-select-recent-institution",
        action="store_true",
        help="Click the first recent institution on Wiley login pages when no institution name is provided.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Process only the first N rows")
    return parser.parse_args()


def sanitize_name(text: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', " ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().replace(" ", "_")
    cleaned = cleaned[:140]
    return cleaned or fallback


def make_target_name(number: str, doi: str) -> str:
    return f"{int(number):03d}_{sanitize_name(doi, f'reference_{number}')}.pdf"


def extract_urls(note: str) -> list[str]:
    urls = []
    for match in URL_RE.findall(note or ""):
        url = match.rstrip(".,);")
        if url not in urls:
            urls.append(url)
    return urls


def choose_article_url(row: dict[str, str]) -> str:
    doi = (row.get("doi") or "").strip()
    urls = extract_urls(row.get("note", ""))
    for url in urls:
        lowered = url.lower()
        if "wiley.com/" in lowered or "doi.org/" in lowered:
            return url
    if doi:
        if doi.lower().startswith("10.1111/"):
            return f"https://onlinelibrary.wiley.com/doi/{quote(doi, safe='/')}"
        return f"https://doi.org/{quote(doi, safe='/')}"
    return urls[0] if urls else ""


def write_utf8_no_bom(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        path.write_bytes(raw[3:])


class DevToolsClient:
    def __init__(self, debug_port: int, viewer_ws_timeout_seconds: int) -> None:
        self.base = f"http://127.0.0.1:{debug_port}"
        self.viewer_ws_timeout_seconds = viewer_ws_timeout_seconds

    def http_get(self, url: str, method: str = "GET") -> str:
        req = Request(url, method=method)
        with urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8")

    def http_get_json(self, url: str, method: str = "GET") -> list[dict] | dict:
        return json.loads(self.http_get(url, method=method))

    def list_pages(self) -> list[dict]:
        pages = self.http_get_json(f"{self.base}/json/list")
        return pages if isinstance(pages, list) else []

    def open_page(self, url: str) -> dict:
        raw = self.http_get_json(f"{self.base}/json/new?{quote(url, safe=':/?&=%')}", method="PUT")
        if not isinstance(raw, dict):
            raise RuntimeError("DevTools did not return a page target")
        return raw

    def close_page(self, page_id: str) -> None:
        try:
            self.http_get(f"{self.base}/json/close/{page_id}")
        except Exception:
            pass

    def call(
        self,
        ws_url: str,
        method: str,
        params: dict | None = None,
        msg_id: int = 1,
        ws_timeout_seconds: int | None = None,
    ) -> dict:
        ws = websocket.create_connection(
            ws_url,
            timeout=ws_timeout_seconds or self.viewer_ws_timeout_seconds,
            suppress_origin=True,
        )
        try:
            ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == msg_id:
                    return msg
        finally:
            ws.close()

    def evaluate(
        self,
        ws_url: str,
        expression: str,
        *,
        await_promise: bool = False,
        msg_id: int = 1,
        ws_timeout_seconds: int | None = None,
    ):
        msg = self.call(
            ws_url,
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
            msg_id=msg_id,
            ws_timeout_seconds=ws_timeout_seconds,
        )
        return msg["result"]["result"].get("value")


def extract_wiley_epdf_link(devtools: DevToolsClient, ws_url: str) -> str:
    value = devtools.evaluate(
        ws_url,
        r"""
JSON.stringify({
  href: location.href,
  links: Array.from(document.querySelectorAll('a[href]')).map(a => ({
    text: (a.innerText || '').trim(),
    href: a.href
  }))
})
        """.strip(),
        msg_id=10,
    )
    payload = json.loads(value or "{}")
    base_href = payload.get("href", "")
    links = payload.get("links", [])
    candidates = []
    for link in links:
        href = (link.get("href") or "").strip()
        text = (link.get("text") or "").strip().lower()
        if not href:
            continue
        full_href = urljoin(base_href, href)
        lowered = full_href.lower()
        if "/doi/epdf/" not in lowered:
            continue
        score = 0
        if text == "pdf":
            score += 3
        if "download pdf" in text:
            score += 2
        candidates.append((score, full_href))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def open_epdf_target_from_article(
    devtools: DevToolsClient,
    article_page: dict,
    epdf_url: str,
    page_wait_seconds: int,
) -> dict | None:
    before_ids = {item["id"] for item in devtools.list_pages()}
    escaped_url = json.dumps(epdf_url)
    devtools.evaluate(
        article_page["webSocketDebuggerUrl"],
        f"""
(() => {{
  const links = Array.from(document.querySelectorAll('a[href]'));
  const href = {escaped_url};
  const link = links.find(a => a.href === href) || links.find(a => /\\/doi\\/epdf\\//i.test(a.href));
  if (!link) return 'NO_EPDF_LINK';
  link.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, view: window }}));
  return link.href;
}})()
        """.strip(),
        msg_id=11,
    )
    deadline = time.time() + max(page_wait_seconds, 8) + 12
    target_url = epdf_url.lower()
    while time.time() < deadline:
        pages = devtools.list_pages()
        for item in pages:
            if item.get("url", "").lower() == target_url:
                return item
        for item in pages:
            if item["id"] in before_ids:
                continue
            if "/doi/epdf/" in item.get("url", "").lower():
                return item
        time.sleep(1)
    return None


def extract_pdfdirect_link(devtools: DevToolsClient, ws_url: str) -> str:
    value = devtools.evaluate(
        ws_url,
        r"""
JSON.stringify(
  Array.from(document.querySelectorAll('a[href]')).map(a => ({
    text: (a.innerText || '').trim(),
    href: a.href
  }))
)
        """.strip(),
        msg_id=20,
    )
    links = json.loads(value or "[]")
    candidates = []
    for link in links:
        href = (link.get("href") or "").strip()
        text = (link.get("text") or "").strip().lower()
        if "/doi/pdfdirect/" not in href.lower():
            continue
        score = 1
        if "download" in text or "get_app" in text:
            score += 2
        candidates.append((score, href))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def submit_shibboleth_consent_if_present(
    devtools: DevToolsClient,
    ws_url: str,
    *,
    institution_name: str = "",
    auto_select_recent_institution: bool = False,
) -> str:
    institution_json = json.dumps(institution_name.strip())
    auto_recent_json = "true" if auto_select_recent_institution else "false"
    script = (
        r"""
(() => {
  const institutionName = __INSTITUTION_NAME__;
  const autoSelectRecentInstitution = __AUTO_SELECT_RECENT__;
  const text = document.body && document.body.innerText ? document.body.innerText : '';
  const forms = Array.from(document.querySelectorAll('form'));
  const isWileyInstitutionChooser =
    /institutional login/i.test(text) &&
    (/recent institutions/i.test(text) || /search for your institution/i.test(text));
  if (isWileyInstitutionChooser) {
    const visible = el => {
      const rect = el.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    };
    const normalized = value => (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
    const institutionNeedle = normalized(institutionName);
    const candidates = Array.from(document.querySelectorAll('a,button,[role="button"],li,div,span'))
      .filter(el => visible(el))
      .map(el => {
        const label = normalized(el.innerText || el.textContent || el.value || '');
        const clickable = el.closest('a,button,[role="button"]') || el;
        return { el, clickable, label };
      })
      .filter(item => item.label);

    if (institutionNeedle) {
      const match = candidates.find(item => item.label.includes(institutionNeedle));
      if (!match) return 'institution_not_found';
      match.clickable.click();
      return 'submitted_institution';
    }

    if (autoSelectRecentInstitution) {
      const recentHeader = candidates.find(item => /recent institutions/i.test(item.label));
      const recentItem = recentHeader
        ? candidates.find(item => {
            if (item.el === recentHeader.el) return false;
            const topDelta = item.el.getBoundingClientRect().top - recentHeader.el.getBoundingClientRect().top;
            return topDelta > 0 && topDelta < 300 && !/edit|search|select your/i.test(item.label);
          })
        : candidates.find(item => !/institutional login|recent institutions|search|select your|edit/i.test(item.label));
      if (!recentItem) return 'institution_selection_required';
      recentItem.clickable.click();
      return 'submitted_institution';
    }

    return 'institution_selection_required';
  }

  const isConsent =
    /information to be provided to service/i.test(text) ||
    /release this information/i.test(text) ||
    /_shib_idp_consent/i.test(document.documentElement.innerHTML || '') ||
    forms.some(form => /consent|profile|saml|shibboleth/i.test(form.action || ''));
  if (!isConsent) return 'not_consent';

  const rememberRadio = document.querySelector(
    'input[type="radio"][name*="consent" i][value*="remember" i]'
  );
  const anyRadio = document.querySelector('input[type="radio"][name*="consent" i]');
  const radio = rememberRadio || anyRadio;
  if (radio) radio.checked = true;

  const controls = Array.from(document.querySelectorAll('button,input[type="submit"],input[type="button"]'));
  const proceed = controls.find(control => {
    const label = [
      control.innerText || '',
      control.value || '',
      control.name || '',
      control.id || '',
      control.getAttribute('aria-label') || ''
    ].join(' ');
    return /proceed|accept|agree|continue|yes|submit/i.test(label) &&
      !/reject|decline|cancel|deny/i.test(label);
  });
  if (proceed) {
    proceed.click();
    return 'submitted';
  }

  const form = forms[0];
  if (form) {
    form.submit();
    return 'submitted_form';
  }
  return 'consent_no_submit';
})()
        """.strip()
        .replace("__INSTITUTION_NAME__", institution_json)
        .replace("__AUTO_SELECT_RECENT__", auto_recent_json)
    )
    value = devtools.evaluate(
        ws_url,
        script,
        msg_id=23,
    )
    return str(value or "")


def extract_pdf_bytes_from_pdfdirect(devtools: DevToolsClient, ws_url: str, pdfdirect_url: str) -> bytes | None:
    escaped_url = json.dumps(pdfdirect_url)
    value = devtools.evaluate(
        ws_url,
        f"""
new Promise(resolve => {{
  const url = {escaped_url};
  const tick = async () => {{
    try {{
      const resp = await fetch(url);
      const buf = await resp.arrayBuffer();
      const data = new Uint8Array(buf);
      if (!resp.ok || data.length < 5) {{
        throw new Error('HTTP_' + resp.status + '_LEN_' + data.length);
      }}
      const chunk = 0x8000;
      let binary = '';
      for (let i = 0; i < data.length; i += chunk) {{
        binary += String.fromCharCode.apply(null, data.subarray(i, i + chunk));
      }}
      if (!binary.startsWith('%PDF-')) {{
        throw new Error('NOT_PDF_' + url);
      }}
      resolve(btoa(binary));
    }} catch (err) {{
      setTimeout(tick, 1000);
    }}
  }};
  tick();
}})
        """.strip(),
        await_promise=True,
        msg_id=21,
        ws_timeout_seconds=devtools.viewer_ws_timeout_seconds,
    )
    if not value or (isinstance(value, str) and value.startswith("ERR:")):
        return None
    return base64.b64decode(value)


def process_row(
    devtools: DevToolsClient,
    row: dict[str, str],
    pdf_dir: Path,
    page_wait_seconds: int,
    institution_name: str,
    auto_select_recent_institution: bool,
) -> dict[str, str]:
    article_url = choose_article_url(row)
    target_name = make_target_name(row["number"], row.get("doi") or row.get("title") or row["number"])
    target_path = pdf_dir / target_name
    if target_path.exists() and target_path.stat().st_size > 0:
        return {
            **row,
            "status": "downloaded",
            "pdf_path": str(target_path),
            "source_url": article_url,
            "note": "existing_file",
        }

    if not article_url:
        return {**row, "status": "no_candidate_urls", "pdf_path": "", "source_url": "", "note": row.get("note", "")}

    article_page = None
    epdf_page = None
    try:
        article_page = devtools.open_page(article_url)
        time.sleep(page_wait_seconds)
        for _ in range(4):
            consent_status = submit_shibboleth_consent_if_present(
                devtools,
                article_page["webSocketDebuggerUrl"],
                institution_name=institution_name,
                auto_select_recent_institution=auto_select_recent_institution,
            )
            if consent_status in {"institution_selection_required", "institution_not_found"}:
                return {
                    **row,
                    "status": consent_status,
                    "pdf_path": "",
                    "source_url": article_url,
                    "note": "Wiley institutional login requires manual selection or a matching --institution-name.",
                }
            if not consent_status.startswith("submitted"):
                break
            time.sleep(page_wait_seconds)
        epdf_url = extract_wiley_epdf_link(devtools, article_page["webSocketDebuggerUrl"])
        if not epdf_url:
            title = devtools.evaluate(article_page["webSocketDebuggerUrl"], "document.title", msg_id=12) or ""
            snippet = devtools.evaluate(
                article_page["webSocketDebuggerUrl"],
                "document.body && document.body.innerText ? document.body.innerText.slice(0, 500) : ''",
                msg_id=13,
            ) or title
            return {
                **row,
                "status": "no_epdf_link",
                "pdf_path": "",
                "source_url": article_url,
                "note": str(snippet)[:500],
            }

        epdf_page = open_epdf_target_from_article(devtools, article_page, epdf_url, page_wait_seconds)
        if not epdf_page:
            return {
                **row,
                "status": "epdf_open_failed",
                "pdf_path": "",
                "source_url": epdf_url,
                "note": "Could not open Wiley ePDF target from the article page",
            }
        time.sleep(page_wait_seconds)
        for _ in range(4):
            consent_status = submit_shibboleth_consent_if_present(
                devtools,
                epdf_page["webSocketDebuggerUrl"],
                institution_name=institution_name,
                auto_select_recent_institution=auto_select_recent_institution,
            )
            if consent_status in {"institution_selection_required", "institution_not_found"}:
                return {
                    **row,
                    "status": consent_status,
                    "pdf_path": "",
                    "source_url": epdf_url,
                    "note": "Wiley institutional login requires manual selection or a matching --institution-name.",
                }
            if not consent_status.startswith("submitted"):
                break
            time.sleep(page_wait_seconds)
        pdfdirect_url = extract_pdfdirect_link(devtools, epdf_page["webSocketDebuggerUrl"])
        if not pdfdirect_url:
            snippet = devtools.evaluate(
                epdf_page["webSocketDebuggerUrl"],
                "document.body && document.body.innerText ? document.body.innerText.slice(0, 800) : ''",
                msg_id=22,
            ) or ""
            return {
                **row,
                "status": "no_pdfdirect_link",
                "pdf_path": "",
                "source_url": epdf_url,
                "note": str(snippet)[:500],
            }
        try:
            pdf_bytes = extract_pdf_bytes_from_pdfdirect(devtools, epdf_page["webSocketDebuggerUrl"], pdfdirect_url)
        except Exception as err:
            return {
                **row,
                "status": "viewer_extract_exception",
                "pdf_path": "",
                "source_url": pdfdirect_url,
                "note": str(err)[:500],
            }
        if not pdf_bytes or not pdf_bytes.startswith(b"%PDF-"):
            return {
                **row,
                "status": "viewer_extract_failed",
                "pdf_path": "",
                "source_url": pdfdirect_url,
                "note": "Wiley PDF extraction failed or returned non-PDF content",
            }

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(pdf_bytes)
        return {
            **row,
            "status": "downloaded",
            "pdf_path": str(target_path),
            "source_url": pdfdirect_url,
            "note": "wiley_pdfdirect_extract",
        }
    finally:
        if article_page:
            devtools.close_page(article_page["id"])
        if epdf_page:
            devtools.close_page(epdf_page["id"])


def main() -> int:
    args = parse_args()
    input_csv = Path(args.input_csv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir = out_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    with input_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    devtools = DevToolsClient(args.debug_port, args.viewer_ws_timeout_seconds)
    results = []
    for index, row in enumerate(rows, start=1):
        doi = row.get("doi", "")
        print(f"[{index}/{len(rows)}] wiley live fetch | {doi}")
        try:
            result = process_row(
                devtools,
                row,
                pdf_dir,
                args.page_wait_seconds,
                args.institution_name,
                args.auto_select_recent_institution,
            )
        except Exception as err:
            result = {
                **row,
                "status": "row_exception",
                "pdf_path": "",
                "source_url": choose_article_url(row),
                "note": str(err)[:500],
            }
        results.append(result)
        print(f"    -> {result['status']}")
        if index < len(rows) and args.inter_item_sleep_seconds > 0:
            print(f"    -> sleeping {args.inter_item_sleep_seconds}s before next row")
            time.sleep(args.inter_item_sleep_seconds)

    fieldnames = ["number", "title", "doi", "year", "journal", "status", "pdf_path", "source_url", "note", "formatted"]
    results_csv = out_dir / "wiley_results.csv"
    missing_csv = out_dir / "wiley_missing.csv"
    downloaded_doi_txt = out_dir / "downloaded_doi.txt"
    missing_doi_txt = out_dir / "missing_doi.txt"
    summary_txt = out_dir / "summary.txt"

    with results_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            writer.writerow({key: row.get(key, "") for key in fieldnames})

    missing_rows = [row for row in results if row.get("status") != "downloaded"]
    with missing_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in missing_rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})

    downloaded_dois = [row.get("doi", "").strip() for row in results if row.get("status") == "downloaded" and row.get("doi", "").strip()]
    missing_dois = [row.get("doi", "").strip() for row in missing_rows if row.get("doi", "").strip()]
    write_utf8_no_bom(downloaded_doi_txt, "\n".join(downloaded_dois) + ("\n" if downloaded_dois else ""))
    write_utf8_no_bom(missing_doi_txt, "\n".join(missing_dois) + ("\n" if missing_dois else ""))

    summary_lines = [
        f"total_rows: {len(results)}",
        f"downloaded: {sum(1 for row in results if row.get('status') == 'downloaded')}",
        f"missing: {sum(1 for row in results if row.get('status') != 'downloaded')}",
        f"output_dir: {out_dir}",
        f"debug_port: {args.debug_port}",
        f"page_wait_seconds: {args.page_wait_seconds}",
        f"inter_item_sleep_seconds: {args.inter_item_sleep_seconds}",
        f"viewer_ws_timeout_seconds: {args.viewer_ws_timeout_seconds}",
    ]
    write_utf8_no_bom(summary_txt, "\n".join(summary_lines) + "\n")
    print("\n".join(summary_lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
