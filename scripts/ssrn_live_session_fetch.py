#!/usr/bin/env python3
"""Fetch SSRN PDFs through a live Edge DevTools session."""

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


URL_RE = re.compile(r"https?://[^\s;,]+", flags=re.I)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch SSRN PDFs through a live Edge DevTools session.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input-csv", required=True, help="Input CSV with SSRN paper rows")
    parser.add_argument("--out-dir", required=True, help="Output directory for raw run artifacts")
    parser.add_argument("--debug-port", type=int, default=9222, help="Edge remote-debugging port")
    parser.add_argument("--page-wait-seconds", type=int, default=10, help="Seconds to wait after opening pages")
    parser.add_argument("--inter-item-sleep-seconds", type=int, default=5, help="Seconds to sleep between rows")
    parser.add_argument("--viewer-ws-timeout-seconds", type=int, default=240, help="PDF extraction timeout")
    parser.add_argument("--limit", type=int, default=0, help="Process only the first N rows")
    return parser.parse_args()


def sanitize_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "_", value or "").strip("_")
    return cleaned[:140] or fallback


def make_target_name(number: str, key: str) -> str:
    return f"{int(number):03d}_{sanitize_name(key, f'reference_{number}')}.pdf"


def extract_urls(note: str) -> list[str]:
    urls: list[str] = []
    for match in URL_RE.findall(note or ""):
        url = match.rstrip(".,);")
        if url not in urls:
            urls.append(url)
    return urls


def ssrn_id_from_row(row: dict[str, str]) -> str:
    for key in ("ssrn_id", "doi", "article_url", "source_url", "note"):
        value = (row.get(key) or "").strip()
        match = re.search(r"10\.2139/ssrn\.(\d+)", value, flags=re.I)
        if match:
            return match.group(1)
        match = re.search(r"abstract(?:_?id)?=(\d+)", value, flags=re.I)
        if match:
            return match.group(1)
    return ""


def choose_article_url(row: dict[str, str]) -> str:
    for key in ("article_url", "source_url"):
        value = (row.get(key) or "").strip()
        if "papers.ssrn.com" in value.lower():
            return value
    for url in extract_urls(row.get("note", "")):
        if "papers.ssrn.com" in url.lower():
            return url
    ssrn_id = ssrn_id_from_row(row)
    if ssrn_id:
        return f"https://papers.ssrn.com/sol3/papers.cfm?abstract_id={ssrn_id}"
    doi = (row.get("doi") or "").strip()
    if doi:
        return f"https://doi.org/{quote(doi, safe='/')}"
    return ""


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

    def open_page(self, url: str) -> dict:
        raw = self.http_get(f"{self.base}/json/new?{quote(url, safe=':/?&=%')}", method="PUT")
        return json.loads(raw)

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
        return msg.get("result", {}).get("result", {}).get("value")


def extract_page_text(devtools: DevToolsClient, ws_url: str) -> str:
    return devtools.evaluate(
        ws_url,
        "document.body ? document.body.innerText : ''",
        msg_id=10,
        ws_timeout_seconds=120,
    ) or ""


def detect_security_block(text: str) -> bool:
    lowered = (text or "").lower()
    return (
        "security verification" in lowered
        or "cloudflare" in lowered
        or "verify you are not a bot" in lowered
        or "checking your browser" in lowered
    )


def find_download_url(devtools: DevToolsClient, ws_url: str, article_url: str) -> str:
    value = devtools.evaluate(
        ws_url,
        """
JSON.stringify(Array.from(document.querySelectorAll('a[href], button, input[type=button], input[type=submit]')).map(el => ({
  tag: el.tagName,
  href: el.href || el.getAttribute('href') || '',
  text: (el.innerText || el.value || el.getAttribute('aria-label') || el.title || '').trim(),
  onclick: el.getAttribute('onclick') || ''
})))
        """.strip(),
        msg_id=11,
        ws_timeout_seconds=120,
    )
    try:
        items = json.loads(value or "[]")
    except json.JSONDecodeError:
        items = []

    scored: list[tuple[int, str]] = []
    for item in items:
        href = (item.get("href") or "").strip()
        onclick = (item.get("onclick") or "").strip()
        text = (item.get("text") or "").strip().lower()
        candidates = [href]
        candidates.extend(extract_urls(onclick))
        for candidate in candidates:
            if not candidate:
                continue
            absolute = urljoin(article_url, candidate)
            lowered = absolute.lower()
            score = 0
            if "delivery.cfm" in lowered:
                score += 5
            if "download" in lowered or "pdf" in lowered:
                score += 2
            if "download" in text:
                score += 3
            if "open pdf" in text or "pdf in browser" in text:
                score += 2
            if score > 0:
                scored.append((score, absolute))

    if not scored:
        return ""
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def fetch_pdf_from_url(devtools: DevToolsClient, ws_url: str, pdf_url: str) -> tuple[bytes | None, str, str]:
    value = devtools.evaluate(
        ws_url,
        f"""
new Promise(resolve => {{
  const url = {json.dumps(pdf_url)};
  fetch(url, {{ credentials: 'include' }}).then(async resp => {{
    const finalUrl = resp.url || url;
    const buf = await resp.arrayBuffer();
    const data = new Uint8Array(buf);
    const chunk = 0x8000;
    let binary = '';
    for (let i = 0; i < data.length; i += chunk) {{
      binary += String.fromCharCode.apply(null, data.subarray(i, i + chunk));
    }}
    if (resp.ok && binary.startsWith('%PDF-')) {{
      resolve(JSON.stringify({{ status: 'ok', source_url: finalUrl, b64: btoa(binary) }}));
    }} else {{
      const text = new TextDecoder().decode(data.slice(0, 3000));
      resolve(JSON.stringify({{ status: 'not_pdf', source_url: finalUrl, text }}));
    }}
  }}).catch(err => resolve(JSON.stringify({{ status: 'fetch_error', source_url: url, text: String(err) }})));
}})
        """.strip(),
        await_promise=True,
        msg_id=21,
        ws_timeout_seconds=devtools.viewer_ws_timeout_seconds,
    )
    if not value:
        return None, pdf_url, "empty_fetch_result"
    payload = json.loads(value)
    if payload.get("status") != "ok":
        return None, payload.get("source_url", pdf_url), payload.get("text", payload.get("status", ""))[:500]
    return base64.b64decode(payload["b64"]), payload.get("source_url", pdf_url), ""


def process_row(
    devtools: DevToolsClient,
    row: dict[str, str],
    pdf_dir: Path,
    page_wait_seconds: int,
) -> dict[str, str]:
    article_url = choose_article_url(row)
    target_name = make_target_name(row["number"], row.get("doi") or row.get("ssrn_id") or row.get("title") or row["number"])
    target_path = pdf_dir / target_name
    if target_path.exists() and target_path.stat().st_size > 0:
        return {**row, "status": "downloaded", "pdf_path": str(target_path), "source_url": article_url, "note": "existing_file"}
    if not article_url:
        return {**row, "status": "no_candidate_urls", "pdf_path": "", "source_url": "", "note": row.get("note", "")}

    article_page = None
    try:
        article_page = devtools.open_page(article_url)
        time.sleep(page_wait_seconds)
        text = extract_page_text(devtools, article_page["webSocketDebuggerUrl"])
        if detect_security_block(text):
            return {
                **row,
                "status": "security_verification",
                "pdf_path": "",
                "source_url": article_url,
                "note": "SSRN security verification page; complete it in Edge and retry the failed subset.",
            }

        pdf_url = find_download_url(devtools, article_page["webSocketDebuggerUrl"], article_url)
        if not pdf_url:
            return {
                **row,
                "status": "no_download_link",
                "pdf_path": "",
                "source_url": article_url,
                "note": text[:500],
            }

        pdf_bytes, final_url, note = fetch_pdf_from_url(devtools, article_page["webSocketDebuggerUrl"], pdf_url)
        if not pdf_bytes or not pdf_bytes.startswith(b"%PDF-"):
            return {
                **row,
                "status": "pdf_fetch_failed",
                "pdf_path": "",
                "source_url": final_url or pdf_url,
                "note": note,
            }

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(pdf_bytes)
        return {
            **row,
            "status": "downloaded",
            "pdf_path": str(target_path),
            "source_url": final_url or pdf_url,
            "note": "ssrn_delivery_fetch",
        }
    finally:
        if article_page:
            devtools.close_page(article_page["id"])


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
        print(f"[{index}/{len(rows)}] ssrn live fetch | {row.get('doi') or row.get('ssrn_id')}")
        try:
            result = process_row(devtools, row, pdf_dir, args.page_wait_seconds)
        except Exception as err:
            result = {**row, "status": "row_exception", "pdf_path": "", "source_url": choose_article_url(row), "note": str(err)[:500]}
        results.append(result)
        print(f"    -> {result.get('status')}")
        if index != len(rows):
            time.sleep(args.inter_item_sleep_seconds)

    fieldnames = ["number", "title", "authors", "doi", "year", "journal", "ssrn_id", "status", "pdf_path", "source_url", "note", "formatted"]
    results_csv = out_dir / "ssrn_results.csv"
    missing_csv = out_dir / "ssrn_missing.csv"
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

    downloaded = sum(1 for item in results if item.get("status") == "downloaded")
    summary_lines = [
        f"total_rows: {len(results)}",
        f"downloaded: {downloaded}",
        f"missing: {len(results) - downloaded}",
        f"output_dir: {out_dir.resolve()}",
        f"debug_port: {args.debug_port}",
        f"results_csv: {results_csv.resolve()}",
        f"missing_csv: {missing_csv.resolve()}",
    ]
    write_utf8_no_bom(out_dir / "summary.txt", "\n".join(summary_lines) + "\n")
    print("\n".join(summary_lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
