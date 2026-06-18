#!/usr/bin/env python3
"""Build and maintain BibTeX paper download artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import time
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

CONTEXT_REL = Path("_runs") / "pipeline_context.json"

ROUTE_LABELS = {
    "sciencedirect": "ScienceDirect / Elsevier",
    "wiley": "Wiley",
    "jstor": "JSTOR",
    "ssrn": "SSRN",
    "unsupported": "Unsupported",
}

SCOPE_RECORD_TYPES = {
    "bib": "bib_paper",
}

MASTER_FIELDS = [
    "record_type",
    "title",
    "authors",
    "journal",
    "journal_abbrev",
    "year",
    "volume",
    "issue",
    "pages_or_article",
    "doi",
    "publisher_platform",
    "article_url",
    "published_status",
    "source_basis",
    "source_confidence",
    "notes",
    "download_supported",
    "preferred_download_route",
    "download_status",
    "primary_output_path",
    "stable_id",
    "stable_url",
    "jstor_status",
]

MANIFEST_FIELDS = [
    "scope",
    "title",
    "authors",
    "journal_abbrev",
    "year",
    "doi",
    "platform",
    "pdf_filename",
    "pdf_path",
    "status",
]

DOWNLOAD_STATUS_SUMMARY_FIELDS = [
    "download_status",
    "count",
]

FAILED_DOWNLOAD_FIELDS = [
    "title",
    "authors",
    "journal",
    "year",
    "doi",
    "preferred_download_route",
    "download_status",
    "notes",
]

PDF_VALIDATION_FIELDS = [
    "title",
    "platform",
    "pdf_filename",
    "pdf_path",
    "exists",
    "starts_with_pdf",
    "file_size",
    "status",
]

BIB_RAW_FIELDS = [
    "bib_key",
    "entry_type",
    "title",
    "authors",
    "journal",
    "year",
    "volume",
    "issue",
    "pages_or_article",
    "doi",
    "publisher_platform",
    "article_url",
    "source_basis",
    "source_confidence",
    "published_status",
    "notes",
]

BIB_SKIPPED_FIELDS = [
    "bib_key",
    "entry_type",
    "title",
    "year",
    "skip_reason",
]

CONFIDENCE_RANK = {"": 0, "low": 1, "medium": 2, "high": 3}
PUBLISHED_STATUS_RANK = {"unknown": 0, "online_in_press": 1, "published": 2}
DOWNLOAD_STATUS_RANK = {
    "": 0,
    "unsupported_platform": 1,
    "pending": 2,
    "failed": 3,
    "downloaded": 4,
}

JOURNAL_ABBREV_MAP = {
    "international economic review": "IER",
    "journal of asset management": "JAM",
    "journal of banking & finance": "JBF",
    "journal of banking and finance": "JBF",
    "journal of econometrics": "JE",
    "journal of finance": "JF",
    "the journal of finance": "JF",
    "journal of financial and quantitative analysis": "JFQA",
    "journal of financial economics": "JFE",
    "journal of financial markets": "JFM",
    "journal of quantitative analysis in sports": "JQAS",
    "journal of risk": "JOR",
    "quarterly journal of economics": "QJE",
    "review of asset pricing studies": "RAPS",
    "review of economic studies": "REStud",
    "review of financial studies": "RFS",
}

WORD_ABBREV_MAP = {
    "american": "A",
    "analysis": "A",
    "asset": "A",
    "banking": "B",
    "business": "Bus",
    "corporate": "Corp",
    "economic": "Econ",
    "econometric": "Ectr",
    "econometrics": "E",
    "economics": "Econ",
    "empirical": "Emp",
    "finance": "F",
    "financial": "F",
    "international": "I",
    "journal": "J",
    "management": "Mgmt",
    "market": "M",
    "markets": "M",
    "policy": "Pol",
    "political": "Pol",
    "quantitative": "Q",
    "review": "R",
    "risk": "Risk",
    "sports": "S",
    "studies": "Stud",
}

STOPWORDS = {"a", "an", "and", "for", "in", "of", "on", "the", "to", "&"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and maintain BibTeX paper-download pipeline outputs.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_bib_parser = subparsers.add_parser(
        "init-bib-run",
        help="Create a BibTeX paper-download run root and initialize canonical output files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    init_bib_parser.add_argument("--bib-file", required=True, help="BibTeX file that anchors the paper batch")
    init_bib_parser.add_argument("--out-dir", required=True, help="Run root directory")
    init_bib_parser.add_argument("--collection-name", default="", help="Optional human-readable name for this paper batch")
    init_bib_parser.add_argument("--force", action="store_true", help="Reinitialize even if the run root already exists")

    import_bib_parser = subparsers.add_parser(
        "import-bib",
        help="Parse @article entries from a BibTeX file and import them into master_catalog.csv.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    import_bib_parser.add_argument("--bib-file", required=True, help="BibTeX file to parse")
    import_bib_parser.add_argument("--master-catalog", required=True, help="Canonical master catalog CSV")
    import_bib_parser.add_argument("--raw-output-csv", default="", help="Where to write parsed @article rows")
    import_bib_parser.add_argument("--skipped-output-csv", default="", help="Where to write skipped non-article rows")
    import_bib_parser.add_argument("--default-source-confidence", default="medium", help="Default source-confidence label")
    import_bib_parser.add_argument("--default-published-status", default="published", help="Default publication-status label")

    enrich_parser = subparsers.add_parser(
        "enrich-dois",
        help="Look up missing DOIs for catalog rows through Crossref and update master_catalog.csv.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    enrich_parser.add_argument("--master-catalog", required=True, help="Canonical master catalog CSV")
    enrich_parser.add_argument("--scope", default="bib", choices=["bib"], help="Catalog scope to enrich")
    enrich_parser.add_argument("--email", default="", help="Optional email for Crossref polite pool")
    enrich_parser.add_argument("--min-title-similarity", type=float, default=0.82, help="Minimum normalized title similarity to accept a DOI")
    enrich_parser.add_argument("--sleep-seconds", type=float, default=0.25, help="Delay between Crossref requests")
    enrich_parser.add_argument("--limit", type=int, default=0, help="Process only the first N missing-DOI rows")

    queue_parser = subparsers.add_parser(
        "build-queues",
        help="Create per-platform queue CSVs from master_catalog.csv.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    queue_parser.add_argument("--master-catalog", required=True, help="Canonical master catalog CSV")
    queue_parser.add_argument("--scope", default="bib", choices=["bib"], help="Queue scope")
    queue_parser.add_argument("--out-dir", required=True, help="Directory where queue CSVs should be written")

    ingest_parser = subparsers.add_parser(
        "ingest-results",
        help="Update master_catalog.csv and materialize final PDFs from platform results.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ingest_parser.add_argument("--master-catalog", required=True, help="Canonical master catalog CSV")
    ingest_parser.add_argument("--download-manifest", required=True, help="Canonical physical-file manifest CSV")
    ingest_parser.add_argument("--scope", default="bib", choices=["bib"], help="Result scope")
    ingest_parser.add_argument("--platform", required=True, choices=["sciencedirect", "wiley", "jstor", "ssrn"], help="Platform that produced the results CSV")
    ingest_parser.add_argument("--results-csv", required=True, help="Platform results CSV to ingest")

    finalize_parser = subparsers.add_parser(
        "finalize-run",
        help="Write final status summaries, failed rows, and PDF validation output for a run root.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    finalize_parser.add_argument("--master-catalog", required=True, help="Canonical master catalog CSV")
    finalize_parser.add_argument("--download-manifest", default="", help="Canonical physical-file manifest CSV")
    finalize_parser.add_argument("--out-dir", default="", help="Directory where final summary CSVs should be written")

    return parser.parse_args()


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def normalize_title(text: str) -> str:
    lowered = normalize_whitespace(text).lower().replace("-", " ")
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def title_similarity(left: str, right: str) -> float:
    left_norm = normalize_title(left)
    right_norm = normalize_title(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def normalize_doi(text: str) -> str:
    doi = normalize_whitespace(text).lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    return doi.strip().strip(".")


def normalize_bool_str(value: str) -> str:
    lowered = normalize_whitespace(value).lower()
    if lowered in {"1", "true", "yes", "y"}:
        return "true"
    if lowered in {"0", "false", "no", "n"}:
        return "false"
    return lowered


def normalize_route(text: str) -> str:
    lowered = normalize_whitespace(text).lower().replace(" ", "").replace("-", "")
    if lowered in {"sciencedirect", "elsevier", "sciencedirect/elsevier"}:
        return "sciencedirect"
    if lowered in {"wiley", "wileyonlinelibrary"}:
        return "wiley"
    if lowered == "jstor":
        return "jstor"
    if lowered == "ssrn":
        return "ssrn"
    return "unsupported"


def canonical_download_status(value: str) -> str:
    lowered = normalize_whitespace(value).lower()
    if lowered in {"downloaded", "success"}:
        return "downloaded"
    if lowered in {"pending", "queued", "not_started"}:
        return "pending"
    if lowered in {"unsupported", "unsupported_platform"}:
        return "unsupported_platform"
    if lowered:
        return "failed"
    return ""


def canonical_published_status(value: str, default: str = "published") -> str:
    lowered = normalize_whitespace(value).lower()
    if not lowered:
        lowered = default.lower()
    if "press" in lowered:
        return "online_in_press"
    if "published" in lowered or lowered in {"journal_article", "article"}:
        return "published"
    return "unknown"


def canonical_confidence(value: str, default: str = "medium") -> str:
    lowered = normalize_whitespace(value).lower()
    if lowered in {"high", "medium", "low"}:
        return lowered
    return default


def parse_year(value: str) -> str:
    match = re.search(r"(19|20)\d{2}", value or "")
    return match.group(0) if match else ""


def parse_volume_issue(row: dict[str, str]) -> tuple[str, str]:
    volume = normalize_whitespace(row.get("volume", ""))
    issue = normalize_whitespace(row.get("issue", ""))
    if volume or issue:
        return volume, issue

    vol_issue = normalize_whitespace(row.get("vol_issue", ""))
    match = re.match(r"(?P<vol>[^()]+)\((?P<issue>[^)]+)\)", vol_issue)
    if match:
        return normalize_whitespace(match.group("vol")), normalize_whitespace(match.group("issue"))
    if vol_issue:
        return vol_issue, ""
    return "", ""


def split_bib_top_level(text: str, separator: str = ",") -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    in_quote = False
    escaped = False
    for index, char in enumerate(text):
        if in_quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_quote = False
            continue
        if char == '"':
            in_quote = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == separator and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def split_bib_assignment(text: str) -> tuple[str, str]:
    depth = 0
    in_quote = False
    escaped = False
    for index, char in enumerate(text):
        if in_quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_quote = False
            continue
        if char == '"':
            in_quote = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == "=" and depth == 0:
            return text[:index].strip().lower(), text[index + 1 :].strip()
    return "", ""


def clean_bib_value(value: str) -> str:
    cleaned = normalize_whitespace(value).rstrip(",")
    while len(cleaned) >= 2 and (
        (cleaned.startswith("{") and cleaned.endswith("}"))
        or (cleaned.startswith('"') and cleaned.endswith('"'))
    ):
        cleaned = normalize_whitespace(cleaned[1:-1])
    cleaned = cleaned.replace("\\&", "&").replace("\\%", "%").replace("\\_", "_")
    cleaned = re.sub(r"\\[\"'`^~=.]\{?([A-Za-z])\}?", r"\1", cleaned)
    cleaned = re.sub(r"\\[a-zA-Z]+\s*", "", cleaned)
    cleaned = cleaned.replace("{", "").replace("}", "")
    return normalize_whitespace(cleaned)


def bib_authors_to_semicolon(authors: str) -> str:
    parts = re.split(r"\s+and\s+", authors or "", flags=re.IGNORECASE)
    return "; ".join(part for part in (normalize_whitespace(part) for part in parts) if part)


def iter_bib_entries(text: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    position = 0
    while True:
        start = text.find("@", position)
        if start < 0:
            return entries
        match = re.match(r"@([A-Za-z]+)\s*([\{\(])", text[start:])
        if not match:
            position = start + 1
            continue
        entry_type = match.group(1).lower()
        opener = match.group(2)
        closer = "}" if opener == "{" else ")"
        index = start + match.end()
        depth = 1
        in_quote = False
        escaped = False
        while index < len(text) and depth > 0:
            char = text[index]
            if in_quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_quote = False
            else:
                if char == '"':
                    in_quote = True
                elif char == opener:
                    depth += 1
                elif char == closer:
                    depth -= 1
            index += 1
        if depth == 0:
            body = text[start + match.end() : index - 1]
            entries.append((entry_type, body))
            position = index
        else:
            position = start + 1


def parse_bib_entry_body(body: str) -> tuple[str, dict[str, str]]:
    parts = split_bib_top_level(body)
    if not parts:
        return "", {}
    bib_key = normalize_whitespace(parts[0])
    fields: dict[str, str] = {}
    for part in parts[1:]:
        field, value = split_bib_assignment(part)
        if not field:
            continue
        fields[field] = clean_bib_value(value)
    return bib_key, fields


def bibtex_to_rows(
    bib_path: Path,
    *,
    default_source_confidence: str,
    default_published_status: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    text = bib_path.read_text(encoding="utf-8-sig")
    raw_rows: list[dict[str, str]] = []
    skipped_rows: list[dict[str, str]] = []

    for entry_type, body in iter_bib_entries(text):
        bib_key, fields = parse_bib_entry_body(body)
        title = fields.get("title", "")
        year = parse_year(fields.get("year", ""))
        if entry_type != "article":
            skipped_rows.append(
                {
                    "bib_key": bib_key,
                    "entry_type": entry_type,
                    "title": title,
                    "year": year,
                    "skip_reason": "non_article_entry",
                }
            )
            continue
        if not title:
            skipped_rows.append(
                {
                    "bib_key": bib_key,
                    "entry_type": entry_type,
                    "title": "",
                    "year": year,
                    "skip_reason": "article_missing_title",
                }
            )
            continue

        notes = join_unique(
            [
                f"bib_key={bib_key}" if bib_key else "",
                fields.get("note", ""),
            ],
            separator=" | ",
        )
        raw_rows.append(
            {
                "bib_key": bib_key,
                "entry_type": entry_type,
                "title": title,
                "authors": bib_authors_to_semicolon(fields.get("author", "")),
                "journal": fields.get("journal", ""),
                "year": year,
                "volume": fields.get("volume", ""),
                "issue": fields.get("number", ""),
                "pages_or_article": fields.get("pages", "") or fields.get("eid", "") or fields.get("article", ""),
                "doi": fields.get("doi", ""),
                "publisher_platform": fields.get("publisher", ""),
                "article_url": fields.get("url", "") or fields.get("article_url", ""),
                "source_basis": "bibtex_article",
                "source_confidence": default_source_confidence,
                "published_status": default_published_status,
                "notes": notes,
            }
        )
    return raw_rows, skipped_rows


def crossref_query(row: dict[str, str], email: str = "") -> dict[str, str]:
    title = normalize_whitespace(row.get("title", ""))
    if not title:
        return {}
    query_parts = [
        title,
        row.get("journal", ""),
        row.get("year", ""),
        row.get("authors", ""),
    ]
    query = " ".join(part for part in (normalize_whitespace(part) for part in query_parts) if part)
    params = {
        "query.bibliographic": query,
        "rows": "5",
    }
    if email:
        params["mailto"] = email
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "bib-paper-fetcher/1.0" + (f" (mailto:{email})" if email else ""),
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    items = payload.get("message", {}).get("items", [])
    return choose_crossref_item(row, items)


def crossref_item_year(item: dict) -> str:
    for key in ("published-print", "published-online", "issued"):
        parts = item.get(key, {}).get("date-parts", [])
        if parts and parts[0]:
            return parse_year(str(parts[0][0]))
    return ""


def choose_crossref_item(row: dict[str, str], items: list[dict]) -> dict[str, str]:
    target_title = row.get("title", "")
    target_year = parse_year(row.get("year", ""))
    best: dict[str, str] = {}
    best_score = -1.0
    for item in items:
        doi = normalize_doi(item.get("DOI", ""))
        titles = item.get("title", [])
        candidate_title = titles[0] if titles else ""
        if not doi or not candidate_title:
            continue
        similarity = title_similarity(target_title, candidate_title)
        candidate_year = crossref_item_year(item)
        year_bonus = 0.0
        if target_year and candidate_year:
            year_bonus = 0.1 if target_year == candidate_year else -0.15
        score = similarity + year_bonus
        if score > best_score:
            best_score = score
            container_titles = item.get("container-title", [])
            best = {
                "doi": doi,
                "title": candidate_title,
                "year": candidate_year,
                "journal": container_titles[0] if container_titles else "",
                "url": item.get("URL", ""),
                "publisher": item.get("publisher", ""),
                "title_similarity": f"{similarity:.3f}",
                "score": f"{score:.3f}",
            }
    return best


def dedupe_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = normalize_whitespace(value)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(cleaned)
    return ordered


def join_unique(parts: list[str], separator: str = "; ") -> str:
    return separator.join(dedupe_preserve(parts))


def sanitize_file_component(text: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', " ", text or "")
    cleaned = normalize_whitespace(cleaned).replace(" ", "_")
    cleaned = cleaned.strip("._")
    cleaned = cleaned[:120]
    return cleaned or fallback


def extract_surname(author_name: str) -> str:
    cleaned = normalize_whitespace(author_name)
    if not cleaned:
        return "Unknown"
    if "," in cleaned:
        return sanitize_file_component(cleaned.split(",", 1)[0], "Unknown")
    parts = cleaned.split(" ")
    return sanitize_file_component(parts[-1], "Unknown")


def author_surnames(authors: str) -> list[str]:
    chunks = re.split(r"\s*;\s*", authors or "")
    surnames = [extract_surname(chunk) for chunk in chunks if normalize_whitespace(chunk)]
    return surnames or ["Unknown"]


def infer_journal_abbrev(journal: str, fallback: str = "Journal") -> str:
    normalized = normalize_whitespace(journal)
    if not normalized:
        return fallback
    mapped = JOURNAL_ABBREV_MAP.get(normalized.lower())
    if mapped:
        return mapped

    tokens = re.findall(r"[A-Za-z0-9&]+", normalized)
    abbreviations: list[str] = []
    for token in tokens:
        lowered = token.lower()
        if lowered in STOPWORDS:
            continue
        abbreviations.append(WORD_ABBREV_MAP.get(lowered, token[:3].title()))
    if not abbreviations:
        return fallback
    result = "".join(part for part in abbreviations[:5])
    return sanitize_file_component(result, fallback)


def filename_journal_abbrev(row: dict[str, str], platform: str = "") -> str:
    route = normalize_route(platform)
    if route == "unsupported":
        route = normalize_route(row.get("preferred_download_route", ""))
    doi = normalize_doi(row.get("doi", ""))
    article_url = normalize_whitespace(row.get("article_url", "") or row.get("source_url", "")).lower()
    journal = normalize_whitespace(row.get("journal", "")).lower()
    if route == "ssrn" or doi.startswith("10.2139/ssrn.") or "papers.ssrn.com" in article_url or "ssrn" in journal:
        return "WP"
    return sanitize_file_component(
        row.get("journal_abbrev") or infer_journal_abbrev(row.get("journal", "")),
        "Journal",
    )


def make_pdf_filename(row: dict[str, str], platform: str = "") -> str:
    surnames = "_".join(author_surnames(row.get("authors", "")))
    journal_abbrev = filename_journal_abbrev(row, platform=platform)
    year = parse_year(row.get("year", "")) or "nd"
    return f"{surnames}_{journal_abbrev}_{year}.pdf"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    ensure_parent(path)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return [{key: value for key, value in row.items()} for row in csv.DictReader(handle)]


def load_context(root_dir: Path) -> dict[str, str]:
    context_path = root_dir / CONTEXT_REL
    if not context_path.exists():
        return {}
    return json.loads(context_path.read_text(encoding="utf-8"))


def write_context(root_dir: Path, payload: dict[str, str]) -> None:
    context_path = root_dir / CONTEXT_REL
    ensure_parent(context_path)
    context_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def derive_stable_id(row: dict[str, str]) -> str:
    direct = normalize_whitespace(row.get("stable_id", ""))
    if direct:
        return direct
    for key in ("stable_url", "article_url", "source_url", "jstor_search_url"):
        value = normalize_whitespace(row.get(key, ""))
        match = re.search(r"/stable/([^/?#]+)", value)
        if match:
            return match.group(1)
    return ""


def derive_article_url(row: dict[str, str]) -> str:
    for key in ("article_url", "source_url", "stable_url"):
        value = normalize_whitespace(row.get(key, ""))
        if value:
            return value
    return ""


def derive_ssrn_id(row: dict[str, str]) -> str:
    candidates = [
        row.get("journal", ""),
        row.get("publisher_platform", ""),
        row.get("platform", ""),
        row.get("article_url", ""),
        row.get("source_url", ""),
        row.get("stable_url", ""),
        row.get("notes", ""),
        row.get("note", ""),
        row.get("doi", ""),
    ]
    for value in candidates:
        text = normalize_whitespace(value)
        text = re.sub(r"crossref_rejected\s+doi=10\.2139/ssrn\.\d+", "", text, flags=re.I)
        match = re.search(r"10\.2139/ssrn\.(\d+)", text, flags=re.I)
        if match:
            return match.group(1)
        match = re.search(r"abstract(?:_?id)?=(\d+)", text, flags=re.I)
        if match:
            return match.group(1)
        match = re.search(r"papers\.ssrn\.com/.+?(\d{5,})", text, flags=re.I)
        if match:
            return match.group(1)
        match = re.search(r"\bssrn\b\D{0,20}(\d{5,})", text, flags=re.I)
        if match:
            return match.group(1)
    return ""


def has_ssrn_route_signal(row: dict[str, str]) -> bool:
    article_url = derive_article_url(row).lower()
    doi = normalize_doi(row.get("doi", ""))
    platform_text = normalize_whitespace(row.get("publisher_platform") or row.get("platform") or "").lower()
    journal = normalize_whitespace(row.get("journal", "")).lower()
    notes = normalize_whitespace(row.get("notes", "") or row.get("note", "")).lower()
    return bool(
        doi.startswith("10.2139/ssrn.")
        or "papers.ssrn.com" in article_url
        or "ssrn" in platform_text
        or "ssrn" in journal
        or "ssrn" in notes
        or derive_ssrn_id(row)
    )


def has_jstor_route_signal(row: dict[str, str]) -> bool:
    article_url = derive_article_url(row).lower()
    jstor_status = normalize_whitespace(row.get("jstor_status", "")).lower()
    return bool(derive_stable_id(row) or "jstor.org" in article_url or jstor_status == "confirmed_on_jstor")


def infer_platform_label(row: dict[str, str]) -> str:
    explicit = normalize_whitespace(row.get("publisher_platform") or row.get("platform") or "")
    if explicit:
        return explicit

    article_url = derive_article_url(row).lower()
    doi = normalize_doi(row.get("doi", ""))

    if has_ssrn_route_signal(row):
        return ROUTE_LABELS["ssrn"]
    if "sciencedirect.com" in article_url or doi.startswith("10.1016/"):
        return ROUTE_LABELS["sciencedirect"]
    if "wiley.com" in article_url or doi.startswith("10.1111/"):
        return ROUTE_LABELS["wiley"]
    if has_jstor_route_signal(row):
        return ROUTE_LABELS["jstor"]
    return "Unknown"


def infer_route(row: dict[str, str]) -> str:
    if has_ssrn_route_signal(row):
        return "ssrn"
    if has_jstor_route_signal(row):
        return "jstor"

    explicit = normalize_route(row.get("preferred_download_route", ""))
    if explicit != "unsupported":
        return explicit

    article_url = derive_article_url(row).lower()
    doi = normalize_doi(row.get("doi", ""))
    platform_text = normalize_whitespace(row.get("publisher_platform") or row.get("platform") or "").lower()

    if "ssrn" in platform_text or "papers.ssrn.com" in article_url or doi.startswith("10.2139/ssrn."):
        return "ssrn"
    if "sciencedirect" in platform_text or "elsevier" in platform_text or "sciencedirect.com" in article_url or doi.startswith("10.1016/"):
        return "sciencedirect"
    if "wiley" in platform_text or "wiley.com" in article_url or doi.startswith("10.1111/"):
        return "wiley"
    return "unsupported"


def truthy_download_supported(route: str, value: str) -> str:
    if route in {"sciencedirect", "wiley", "jstor", "ssrn"}:
        return "true"
    explicit = normalize_bool_str(value)
    if explicit in {"true", "false"}:
        return explicit
    return "false"


def refresh_download_fields(row: dict[str, str]) -> None:
    row["jstor_status"] = choose_jstor_status("", row.get("jstor_status", ""))
    if derive_stable_id(row):
        row["jstor_status"] = "confirmed_on_jstor"
    route = infer_route(row)
    row["preferred_download_route"] = route
    row["download_supported"] = truthy_download_supported(route, row.get("download_supported", ""))
    status = canonical_download_status(row.get("download_status", ""))
    if status == "downloaded":
        row["download_status"] = status
        return
    if row["download_supported"] == "true":
        row["download_status"] = "pending" if status == "unsupported_platform" else (status or "pending")
    else:
        row["download_status"] = "unsupported_platform"


def build_formatted_citation(row: dict[str, str]) -> str:
    authors = normalize_whitespace(row.get("authors", ""))
    year = parse_year(row.get("year", ""))
    title = normalize_whitespace(row.get("title", ""))
    journal = normalize_whitespace(row.get("journal", ""))
    volume = normalize_whitespace(row.get("volume", ""))
    issue = normalize_whitespace(row.get("issue", ""))
    pages = normalize_whitespace(row.get("pages_or_article", ""))
    vol_issue = volume
    if volume and issue:
        vol_issue = f"{volume}({issue})"
    pieces = [authors]
    if year:
        pieces.append(f"({year}).")
    if title:
        pieces.append(f"{title}.")
    if journal:
        if vol_issue:
            pieces.append(f"{journal}, {vol_issue}")
        else:
            pieces.append(journal)
    if pages:
        pieces.append(pages)
    return " ".join(piece for piece in pieces if piece).strip()


def make_record_key(row: dict[str, str]) -> str:
    record_type = row.get("record_type", "")
    doi = normalize_doi(row.get("doi", ""))
    stable_id = normalize_whitespace(row.get("stable_id", ""))
    title = normalize_title(row.get("title", ""))
    year = parse_year(row.get("year", ""))
    if doi:
        return f"{record_type}|doi|{doi}"
    if stable_id:
        return f"{record_type}|stable|{stable_id}"
    return f"{record_type}|title|{title}|{year}"


def choose_value(existing: str, new_value: str) -> str:
    return normalize_whitespace(existing) or normalize_whitespace(new_value)


def choose_jstor_status(existing: str, new_value: str) -> str:
    current = normalize_whitespace(existing)
    incoming = normalize_whitespace(new_value)
    if incoming.lower() == "confirmed_on_jstor":
        return "confirmed_on_jstor"
    if current.lower() == "confirmed_on_jstor":
        return "confirmed_on_jstor"
    return current or incoming


def choose_better(existing: str, new_value: str, rank_map: dict[str, int], default: str = "") -> str:
    current = normalize_whitespace(existing).lower()
    incoming = normalize_whitespace(new_value).lower()
    if rank_map.get(incoming, rank_map.get(default, 0)) >= rank_map.get(current, rank_map.get(default, 0)):
        return incoming or current
    return current


def normalize_input_row(
    raw: dict[str, str],
    *,
    record_type: str,
    default_source_basis: str,
    default_source_confidence: str,
    default_published_status: str,
) -> dict[str, str]:
    title = normalize_whitespace(raw.get("title", ""))
    if not title:
        return {}

    status_value = normalize_whitespace(raw.get("published_status", ""))
    if not status_value:
        candidate = normalize_whitespace(raw.get("status", ""))
        if any(token in candidate.lower() for token in ("published", "press", "forthcoming")):
            status_value = candidate

    download_status = normalize_whitespace(raw.get("download_status", ""))
    if not download_status:
        candidate = normalize_whitespace(raw.get("status", ""))
        if candidate and candidate.lower() in {"downloaded", "failed", "pending", "unsupported_platform"}:
            download_status = candidate

    volume, issue = parse_volume_issue(raw)
    article_url = derive_article_url(raw)
    stable_id = derive_stable_id(raw)
    stable_url = normalize_whitespace(raw.get("stable_url", ""))
    if not stable_url and stable_id:
        stable_url = f"https://www.jstor.org/stable/{stable_id}"

    normalized = {field: "" for field in MASTER_FIELDS}
    normalized["record_type"] = record_type
    normalized["title"] = title
    normalized["authors"] = normalize_whitespace(raw.get("authors", ""))
    normalized["journal"] = normalize_whitespace(raw.get("journal", ""))
    normalized["journal_abbrev"] = normalize_whitespace(raw.get("journal_abbrev", "")) or infer_journal_abbrev(
        raw.get("journal", ""),
        fallback="Journal",
    )
    normalized["year"] = parse_year(raw.get("year", ""))
    normalized["volume"] = volume
    normalized["issue"] = issue
    normalized["pages_or_article"] = normalize_whitespace(
        raw.get("pages_or_article", "") or raw.get("pages", "") or raw.get("article_number", "")
    )
    normalized["doi"] = normalize_doi(raw.get("doi", ""))
    normalized["publisher_platform"] = infer_platform_label(raw)
    normalized["article_url"] = article_url or stable_url
    normalized["published_status"] = canonical_published_status(status_value, default=default_published_status)
    normalized["source_basis"] = normalize_whitespace(raw.get("source_basis", "")) or default_source_basis
    normalized["source_confidence"] = canonical_confidence(
        raw.get("source_confidence", ""),
        default=default_source_confidence,
    )
    normalized["notes"] = normalize_whitespace(raw.get("notes", "") or raw.get("note", ""))
    normalized["download_supported"] = normalize_bool_str(raw.get("download_supported", ""))
    normalized["preferred_download_route"] = normalize_whitespace(raw.get("preferred_download_route", ""))
    normalized["download_status"] = download_status
    normalized["primary_output_path"] = normalize_whitespace(raw.get("primary_output_path", ""))
    normalized["stable_id"] = stable_id
    normalized["stable_url"] = stable_url
    normalized["jstor_status"] = normalize_whitespace(raw.get("jstor_status", ""))
    refresh_download_fields(normalized)
    return normalized


def merge_rows(existing: dict[str, str], incoming: dict[str, str]) -> dict[str, str]:
    merged = existing.copy()
    simple_fields = [
        "title",
        "authors",
        "journal",
        "journal_abbrev",
        "year",
        "volume",
        "issue",
        "pages_or_article",
        "doi",
        "publisher_platform",
        "article_url",
        "primary_output_path",
        "stable_id",
        "stable_url",
    ]
    for field in simple_fields:
        merged[field] = choose_value(existing.get(field, ""), incoming.get(field, ""))
    merged["jstor_status"] = choose_jstor_status(existing.get("jstor_status", ""), incoming.get("jstor_status", ""))

    merged["published_status"] = choose_better(
        existing.get("published_status", ""),
        incoming.get("published_status", ""),
        PUBLISHED_STATUS_RANK,
    )
    merged["source_confidence"] = choose_better(
        existing.get("source_confidence", ""),
        incoming.get("source_confidence", ""),
        CONFIDENCE_RANK,
        default="medium",
    )
    merged["download_status"] = choose_better(
        existing.get("download_status", ""),
        incoming.get("download_status", ""),
        DOWNLOAD_STATUS_RANK,
    )
    merged["source_basis"] = join_unique(
        [existing.get("source_basis", ""), incoming.get("source_basis", "")],
        separator="; ",
    )
    merged["notes"] = join_unique(
        [existing.get("notes", ""), incoming.get("notes", "")],
        separator=" | ",
    )
    refresh_download_fields(merged)
    if merged["download_status"] == "downloaded" and existing.get("primary_output_path"):
        merged["primary_output_path"] = existing["primary_output_path"]
    return merged


def load_master_catalog(path: Path) -> list[dict[str, str]]:
    rows = read_csv_rows(path)
    normalized_rows: list[dict[str, str]] = []
    for row in rows:
        normalized = {field: normalize_whitespace(row.get(field, "")) for field in MASTER_FIELDS}
        refresh_download_fields(normalized)
        normalized_rows.append(normalized)
    return normalized_rows


def load_manifest(path: Path) -> list[dict[str, str]]:
    rows = read_csv_rows(path)
    return [{field: normalize_whitespace(row.get(field, "")) for field in MANIFEST_FIELDS} for row in rows]


def manifest_key(row: dict[str, str]) -> str:
    return "|".join(
        [
            normalize_whitespace(row.get("scope", "")).lower(),
            normalize_doi(row.get("doi", "")) or normalize_title(row.get("title", "")),
        ]
    )


def allocate_target_path(desired: Path, existing_path: str = "") -> Path:
    if existing_path:
        return Path(existing_path)
    if not desired.exists():
        return desired
    stem = desired.stem
    suffix = desired.suffix
    for index in range(2, 1000):
        candidate = desired.with_name(f"{stem}__{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not allocate a unique target path under {desired.parent}")


def upsert_manifest(manifest_rows: list[dict[str, str]], entry: dict[str, str]) -> dict[str, str]:
    key = manifest_key(entry)
    for index, row in enumerate(manifest_rows):
        if manifest_key(row) == key:
            manifest_rows[index] = {field: entry.get(field, "") for field in MANIFEST_FIELDS}
            return manifest_rows[index]
    manifest_rows.append({field: entry.get(field, "") for field in MANIFEST_FIELDS})
    return manifest_rows[-1]


def root_dir_from_master(master_catalog_path: Path) -> Path:
    return master_catalog_path.resolve().parent


def record_type_for_scope(scope: str) -> str:
    if scope not in SCOPE_RECORD_TYPES:
        raise ValueError(f"Unsupported scope: {scope}")
    return SCOPE_RECORD_TYPES[scope]


def papers_dir_from_context(root_dir: Path) -> Path:
    context = load_context(root_dir)
    papers_dir_name = context.get("papers_dir_name") or "papers"
    return root_dir / papers_dir_name


def copy_pdf(src: Path, dest_dir: Path, filename: str, existing_path: str = "") -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    desired = dest_dir / filename
    existing = Path(existing_path) if existing_path else None
    if existing and existing.exists() and existing.name == filename:
        target = existing
    else:
        target = allocate_target_path(desired)
    shutil.copy2(src, target)
    if existing and existing.exists() and existing.resolve() != target.resolve():
        try:
            if existing.parent.resolve() == dest_dir.resolve():
                existing.unlink()
        except OSError:
            pass
    return target


def find_matching_row(
    master_rows: list[dict[str, str]],
    *,
    scope: str,
    result_row: dict[str, str],
) -> dict[str, str] | None:
    target_record_type = record_type_for_scope(scope)
    doi = normalize_doi(result_row.get("doi", ""))
    stable_id = normalize_whitespace(result_row.get("stable_id", "")) or derive_stable_id(result_row)
    title = normalize_title(result_row.get("title", ""))

    for row in master_rows:
        if row.get("record_type") != target_record_type:
            continue
        if doi and normalize_doi(row.get("doi", "")) == doi:
            return row
    for row in master_rows:
        if row.get("record_type") != target_record_type:
            continue
        if stable_id and normalize_whitespace(row.get("stable_id", "")) == stable_id:
            return row
    for row in master_rows:
        if row.get("record_type") != target_record_type:
            continue
        if title and normalize_title(row.get("title", "")) == title:
            return row
    return None


def append_note(row: dict[str, str], note: str) -> None:
    row["notes"] = join_unique([row.get("notes", ""), note], separator=" | ")


def sort_master_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    def sort_key(row: dict[str, str]) -> tuple[int, int, str]:
        record_rank = {
            "bib_paper": 0,
        }.get(row.get("record_type"), 9)
        year = parse_year(row.get("year", "")) or "9999"
        return (record_rank, int(year), normalize_title(row.get("title", "")))

    return sorted(rows, key=sort_key)


def queue_row_for_science_like(record: dict[str, str], number: int) -> dict[str, str]:
    return {
        "number": str(number),
        "title": record.get("title", ""),
        "doi": record.get("doi", ""),
        "year": record.get("year", ""),
        "journal": record.get("journal", ""),
        "note": record.get("article_url", "") or record.get("stable_url", ""),
        "formatted": build_formatted_citation(record),
    }


def queue_row_for_jstor(record: dict[str, str], number: int) -> dict[str, str]:
    stable_id = record.get("stable_id", "")
    stable_url = record.get("stable_url", "") or (
        f"https://www.jstor.org/stable/{stable_id}" if stable_id else ""
    )
    article_url = record.get("article_url", "") or stable_url
    return {
        "number": str(number),
        "ref_no": str(number),
        "title": record.get("title", ""),
        "authors": record.get("authors", ""),
        "year": record.get("year", ""),
        "journal": record.get("journal", ""),
        "doi": record.get("doi", ""),
        "stable_id": stable_id,
        "stable_url": stable_url,
        "article_url": article_url,
        "source_url": article_url,
        "jstor_status": record.get("jstor_status", ""),
        "note": record.get("notes", ""),
    }


def queue_row_for_ssrn(record: dict[str, str], number: int) -> dict[str, str]:
    ssrn_id = derive_ssrn_id(record)
    article_url = record.get("article_url", "")
    doi_ssrn_match = re.search(r"10\.2139/ssrn\.(\d+)", article_url, flags=re.I)
    if ssrn_id and (not article_url or (doi_ssrn_match and doi_ssrn_match.group(1) != ssrn_id)):
        article_url = f"https://papers.ssrn.com/sol3/papers.cfm?abstract_id={ssrn_id}"
    return {
        "number": str(number),
        "title": record.get("title", ""),
        "authors": record.get("authors", ""),
        "doi": record.get("doi", ""),
        "year": record.get("year", ""),
        "journal": record.get("journal", ""),
        "ssrn_id": ssrn_id,
        "article_url": article_url,
        "source_url": article_url,
        "note": record.get("article_url", "") or record.get("notes", ""),
        "formatted": build_formatted_citation(record),
    }


def queue_row_for_unsupported(record: dict[str, str], number: int) -> dict[str, str]:
    return {
        "number": str(number),
        "title": record.get("title", ""),
        "authors": record.get("authors", ""),
        "year": record.get("year", ""),
        "journal": record.get("journal", ""),
        "doi": record.get("doi", ""),
        "publisher_platform": record.get("publisher_platform", ""),
        "article_url": record.get("article_url", ""),
        "preferred_download_route": record.get("preferred_download_route", ""),
        "download_status": record.get("download_status", ""),
        "notes": record.get("notes", ""),
    }


def jstor_row_has_known_stable(row: dict[str, str]) -> bool:
    return bool(derive_stable_id(row))


def write_recommended_download_order(
    out_dir: Path,
    *,
    jstor_known_count: int,
    wiley_count: int,
    jstor_search_count: int,
    ssrn_count: int,
    sciencedirect_count: int,
) -> Path:
    order_path = out_dir / "recommended_download_order.txt"
    lines = [
        "Recommended mixed-source batch order",
        "",
        "Run the available queue files in this order:",
        f"1. jstor_input_known_stable.csv ({jstor_known_count} rows)",
        "   JSTOR rows that already have a stable_id or stable_url.",
        f"2. wiley_input.csv ({wiley_count} rows)",
        "   Wiley rows after the known-stable JSTOR batch is complete.",
        f"3. jstor_input_search.csv ({jstor_search_count} rows)",
        "   Remaining JSTOR rows that still require title-search resolution.",
        f"4. ssrn_input.csv ({ssrn_count} rows)",
        "   SSRN rows, including 10.2139/ssrn DOI or papers.ssrn.com records.",
        f"5. sciencedirect_input.csv ({sciencedirect_count} rows)",
        "   ScienceDirect / Elsevier rows as the final publisher batch.",
        "",
        "Notes:",
        "- Skip any file that was not written for this queue build.",
        "- jstor_input.csv remains as the backward-compatible all-JSTOR file.",
    ]
    order_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return order_path


def cmd_init_bib_run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir).resolve()
    bib_file = Path(args.bib_file).resolve()
    collection_name = normalize_whitespace(args.collection_name) or bib_file.stem
    directories = [
        out_dir,
        out_dir / "papers",
        out_dir / "_runs" / "discovery",
        out_dir / "_runs" / "queues" / "bib",
        out_dir / "_runs" / "platform" / "bib",
    ]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

    master_catalog = out_dir / "master_catalog.csv"
    download_manifest = out_dir / "download_manifest.csv"
    if args.force or not master_catalog.exists():
        write_csv(master_catalog, MASTER_FIELDS, [])
    if args.force or not download_manifest.exists():
        write_csv(download_manifest, MANIFEST_FIELDS, [])

    write_context(
        out_dir,
        {
            "collection_name": collection_name,
            "bib_file": str(bib_file),
            "papers_dir_name": "papers",
        },
    )

    print(f"Initialized BibTeX run root: {out_dir}")
    print(f"BibTeX file: {bib_file}")
    print(f"Papers folder: {out_dir / 'papers'}")
    print(f"Master catalog: {master_catalog}")
    print(f"Download manifest: {download_manifest}")
    return 0


def cmd_import_bib(args: argparse.Namespace) -> int:
    bib_path = Path(args.bib_file).resolve()
    master_catalog_path = Path(args.master_catalog).resolve()
    root_dir = root_dir_from_master(master_catalog_path)
    raw_output = Path(args.raw_output_csv).resolve() if args.raw_output_csv else root_dir / "_runs" / "discovery" / "bib_articles_raw.csv"
    skipped_output = Path(args.skipped_output_csv).resolve() if args.skipped_output_csv else root_dir / "_runs" / "discovery" / "bib_skipped.csv"

    raw_rows, skipped_rows = bibtex_to_rows(
        bib_path,
        default_source_confidence=args.default_source_confidence,
        default_published_status=args.default_published_status,
    )
    write_csv(raw_output, BIB_RAW_FIELDS, raw_rows)
    write_csv(skipped_output, BIB_SKIPPED_FIELDS, skipped_rows)

    if not master_catalog_path.exists():
        write_csv(master_catalog_path, MASTER_FIELDS, [])

    master_rows = load_master_catalog(master_catalog_path)
    keyed_rows = {make_record_key(row): row for row in master_rows}
    imported = 0
    skipped_without_title = 0
    for raw in raw_rows:
        normalized = normalize_input_row(
            raw,
            record_type="bib_paper",
            default_source_basis="bibtex_article",
            default_source_confidence=args.default_source_confidence,
            default_published_status=args.default_published_status,
        )
        if not normalized:
            skipped_without_title += 1
            continue
        key = make_record_key(normalized)
        if key in keyed_rows:
            keyed_rows[key] = merge_rows(keyed_rows[key], normalized)
        else:
            keyed_rows[key] = normalized
        imported += 1

    write_csv(master_catalog_path, MASTER_FIELDS, sort_master_rows(list(keyed_rows.values())))
    print(f"BibTeX file: {bib_path}")
    print(f"Article rows parsed: {len(raw_rows)}")
    print(f"Non-article or invalid entries skipped: {len(skipped_rows)}")
    print(f"Imported rows: {imported}")
    print(f"Skipped article rows without title: {skipped_without_title}")
    print(f"Raw article CSV: {raw_output}")
    print(f"Skipped CSV: {skipped_output}")
    print(f"Master catalog updated: {master_catalog_path}")
    return 0


def cmd_enrich_dois(args: argparse.Namespace) -> int:
    master_catalog_path = Path(args.master_catalog).resolve()
    master_rows = load_master_catalog(master_catalog_path)
    target_record_type = record_type_for_scope(args.scope)

    candidates = [
        row
        for row in master_rows
        if row.get("record_type") == target_record_type and not normalize_doi(row.get("doi", ""))
    ]
    if args.limit and args.limit > 0:
        candidates = candidates[: args.limit]

    looked_up = 0
    enriched = 0
    rejected = 0
    failed = 0
    for row in candidates:
        looked_up += 1
        title = row.get("title", "")
        print(f"[{looked_up}/{len(candidates)}] Crossref DOI lookup | {title}")
        try:
            match = crossref_query(row, email=args.email)
        except Exception as err:
            failed += 1
            append_note(row, f"crossref_lookup_failed={str(err)[:180]}")
            print(f"    -> lookup_failed: {err}")
            continue

        similarity = float(match.get("title_similarity", "0") or 0)
        if not match or similarity < args.min_title_similarity:
            rejected += 1
            note = "crossref_no_accepted_match"
            if match:
                note = f"crossref_rejected doi={match.get('doi', '')} similarity={match.get('title_similarity', '')}"
            append_note(row, note)
            print(f"    -> rejected ({match.get('title_similarity', '0') if match else 'no_match'})")
        else:
            row["doi"] = match["doi"]
            if not row.get("article_url"):
                row["article_url"] = match.get("url", "") or f"https://doi.org/{match['doi']}"
            if not row.get("publisher_platform") or row.get("publisher_platform") == "Unknown":
                row["publisher_platform"] = match.get("publisher", "")
            append_note(row, f"crossref_doi={match['doi']} similarity={match.get('title_similarity', '')}")
            refresh_download_fields(row)
            enriched += 1
            print(f"    -> {match['doi']} similarity={match.get('title_similarity', '')}")

        if args.sleep_seconds > 0 and looked_up < len(candidates):
            time.sleep(args.sleep_seconds)

    write_csv(master_catalog_path, MASTER_FIELDS, sort_master_rows(master_rows))
    print(f"Scope: {args.scope}")
    print(f"Missing-DOI rows checked: {looked_up}")
    print(f"Rows enriched: {enriched}")
    print(f"Rows rejected: {rejected}")
    print(f"Lookup failures: {failed}")
    print(f"Master catalog updated: {master_catalog_path}")
    return 0


def cmd_build_queues(args: argparse.Namespace) -> int:
    master_catalog_path = Path(args.master_catalog).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    master_rows = load_master_catalog(master_catalog_path)
    target_record_type = record_type_for_scope(args.scope)

    eligible_rows = []
    unsupported_records: list[dict[str, str]] = []
    for row in master_rows:
        refresh_download_fields(row)
        if row.get("record_type") != target_record_type:
            continue
        if row.get("download_supported") != "true":
            if row.get("download_status") != "downloaded":
                unsupported_records.append(row)
            continue
        if row.get("download_status") == "downloaded":
            continue
        eligible_rows.append(row)

    write_csv(master_catalog_path, MASTER_FIELDS, sort_master_rows(master_rows))

    sciencedirect_records: list[dict[str, str]] = []
    wiley_records: list[dict[str, str]] = []
    jstor_records: list[dict[str, str]] = []
    ssrn_records: list[dict[str, str]] = []
    for record in eligible_rows:
        route = record.get("preferred_download_route", "")
        if route == "sciencedirect":
            sciencedirect_records.append(record)
        elif route == "wiley":
            wiley_records.append(record)
        elif route == "jstor":
            jstor_records.append(record)
        elif route == "ssrn":
            ssrn_records.append(record)

    jstor_known_records = [record for record in jstor_records if jstor_row_has_known_stable(record)]
    jstor_search_records = [record for record in jstor_records if not jstor_row_has_known_stable(record)]

    sciencedirect_rows = [
        queue_row_for_science_like(record, index)
        for index, record in enumerate(sciencedirect_records, start=1)
    ]
    wiley_rows = [
        queue_row_for_science_like(record, index)
        for index, record in enumerate(wiley_records, start=1)
    ]
    jstor_rows = [
        queue_row_for_jstor(record, index)
        for index, record in enumerate(jstor_known_records + jstor_search_records, start=1)
    ]
    jstor_known_rows = [
        queue_row_for_jstor(record, index)
        for index, record in enumerate(jstor_known_records, start=1)
    ]
    jstor_search_rows = [
        queue_row_for_jstor(record, index)
        for index, record in enumerate(jstor_search_records, start=1)
    ]
    ssrn_rows = [
        queue_row_for_ssrn(record, index)
        for index, record in enumerate(ssrn_records, start=1)
    ]
    unsupported_rows = [
        queue_row_for_unsupported(record, index)
        for index, record in enumerate(unsupported_records, start=1)
    ]

    outputs = [
        ("sciencedirect_input.csv", ["number", "title", "doi", "year", "journal", "note", "formatted"], sciencedirect_rows),
        ("wiley_input.csv", ["number", "title", "doi", "year", "journal", "note", "formatted"], wiley_rows),
        ("ssrn_input.csv", ["number", "title", "authors", "doi", "year", "journal", "ssrn_id", "article_url", "source_url", "note", "formatted"], ssrn_rows),
        ("unsupported.csv", ["number", "title", "authors", "year", "journal", "doi", "publisher_platform", "article_url", "preferred_download_route", "download_status", "notes"], unsupported_rows),
        (
            "jstor_input.csv",
            ["number", "ref_no", "title", "authors", "year", "journal", "doi", "stable_id", "stable_url", "article_url", "source_url", "jstor_status", "note"],
            jstor_rows,
        ),
        (
            "jstor_input_known_stable.csv",
            ["number", "ref_no", "title", "authors", "year", "journal", "doi", "stable_id", "stable_url", "article_url", "source_url", "jstor_status", "note"],
            jstor_known_rows,
        ),
        (
            "jstor_input_search.csv",
            ["number", "ref_no", "title", "authors", "year", "journal", "doi", "stable_id", "stable_url", "article_url", "source_url", "jstor_status", "note"],
            jstor_search_rows,
        ),
    ]
    for filename, fieldnames, rows in outputs:
        target = out_dir / filename
        if rows:
            write_csv(target, fieldnames, rows)
        elif target.exists():
            target.unlink()

    order_path = write_recommended_download_order(
        out_dir,
        jstor_known_count=len(jstor_known_rows),
        wiley_count=len(wiley_rows),
        jstor_search_count=len(jstor_search_rows),
        ssrn_count=len(ssrn_rows),
        sciencedirect_count=len(sciencedirect_rows),
    )

    print(f"Queue scope: {args.scope}")
    print(f"ScienceDirect rows: {len(sciencedirect_rows)}")
    print(f"Wiley rows: {len(wiley_rows)}")
    print(f"SSRN rows: {len(ssrn_rows)}")
    print(f"JSTOR rows: {len(jstor_rows)}")
    print(f"JSTOR known-stable rows: {len(jstor_known_rows)}")
    print(f"JSTOR search rows: {len(jstor_search_rows)}")
    print(f"Unsupported rows: {len(unsupported_rows)}")
    print(f"Recommended order file: {order_path}")
    print(f"Queue directory: {out_dir}")
    return 0


def cmd_ingest_results(args: argparse.Namespace) -> int:
    master_catalog_path = Path(args.master_catalog).resolve()
    manifest_path = Path(args.download_manifest).resolve()
    results_path = Path(args.results_csv).resolve()
    root_dir = root_dir_from_master(master_catalog_path)
    papers_dir = papers_dir_from_context(root_dir)

    master_rows = load_master_catalog(master_catalog_path)
    manifest_rows = load_manifest(manifest_path)
    result_rows = read_csv_rows(results_path)

    success_count = 0
    failure_count = 0
    unmatched_count = 0

    for result in result_rows:
        matched = find_matching_row(master_rows, scope=args.scope, result_row=result)
        if not matched:
            unmatched_count += 1
            continue

        status = canonical_download_status(result.get("status", ""))
        if status != "downloaded":
            matched["download_status"] = status or "failed"
            append_note(matched, f"{args.platform}_result={result.get('status', '')}")
            failure_count += 1
            continue

        raw_pdf_path = Path(result.get("pdf_path", "")).resolve()
        if not raw_pdf_path.exists():
            matched["download_status"] = "failed"
            append_note(matched, f"{args.platform}_pdf_missing={raw_pdf_path}")
            failure_count += 1
            continue

        pdf_filename = make_pdf_filename(matched, platform=args.platform)
        final_path = copy_pdf(
            raw_pdf_path,
            papers_dir,
            pdf_filename,
            existing_path=matched.get("primary_output_path", ""),
        )
        final_journal_abbrev = filename_journal_abbrev(matched, platform=args.platform)
        manifest_entry = {
            "scope": "bib",
            "title": matched.get("title", ""),
            "authors": matched.get("authors", ""),
            "journal_abbrev": final_journal_abbrev,
            "year": matched.get("year", ""),
            "doi": matched.get("doi", ""),
            "platform": args.platform,
            "pdf_filename": final_path.name,
            "pdf_path": str(final_path),
            "status": "downloaded",
        }
        upsert_manifest(manifest_rows, manifest_entry)

        matched["download_status"] = "downloaded"
        matched["primary_output_path"] = str(final_path)
        matched["journal_abbrev"] = final_journal_abbrev
        success_count += 1

    write_csv(master_catalog_path, MASTER_FIELDS, sort_master_rows(master_rows))
    write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)
    print(f"Ingest scope: {args.scope}")
    print(f"Platform: {args.platform}")
    print(f"Successful rows: {success_count}")
    print(f"Failed rows: {failure_count}")
    print(f"Unmatched result rows: {unmatched_count}")
    print(f"Master catalog updated: {master_catalog_path}")
    print(f"Download manifest updated: {manifest_path}")
    return 0


def validate_pdf_manifest_row(row: dict[str, str]) -> dict[str, str]:
    pdf_path = Path(row.get("pdf_path", ""))
    exists = pdf_path.exists() and pdf_path.is_file()
    starts_with_pdf = False
    file_size = 0
    if exists:
        file_size = pdf_path.stat().st_size
        with pdf_path.open("rb") as handle:
            starts_with_pdf = handle.read(5) == b"%PDF-"
    status = "ok" if exists and starts_with_pdf and file_size > 0 else "invalid"
    return {
        "title": row.get("title", ""),
        "platform": row.get("platform", ""),
        "pdf_filename": row.get("pdf_filename", ""),
        "pdf_path": row.get("pdf_path", ""),
        "exists": "true" if exists else "false",
        "starts_with_pdf": "true" if starts_with_pdf else "false",
        "file_size": str(file_size),
        "status": status,
    }


def cmd_finalize_run(args: argparse.Namespace) -> int:
    master_catalog_path = Path(args.master_catalog).resolve()
    root_dir = root_dir_from_master(master_catalog_path)
    manifest_path = Path(args.download_manifest).resolve() if args.download_manifest else root_dir / "download_manifest.csv"
    out_dir = Path(args.out_dir).resolve() if args.out_dir else root_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    master_rows = load_master_catalog(master_catalog_path)
    manifest_rows = load_manifest(manifest_path)

    status_counts: dict[str, int] = {}
    for row in master_rows:
        status = canonical_download_status(row.get("download_status", "")) or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1

    summary_rows = [
        {"download_status": status, "count": str(count)}
        for status, count in sorted(status_counts.items(), key=lambda item: item[0])
    ]
    failed_rows = [
        {field: row.get(field, "") for field in FAILED_DOWNLOAD_FIELDS}
        for row in master_rows
        if row.get("download_status", "") != "downloaded"
    ]
    validation_rows = [validate_pdf_manifest_row(row) for row in manifest_rows]

    summary_path = out_dir / "download_status_summary.csv"
    failed_path = out_dir / "failed_downloads.csv"
    validation_path = out_dir / "pdf_validation.csv"

    write_csv(summary_path, DOWNLOAD_STATUS_SUMMARY_FIELDS, summary_rows)
    write_csv(failed_path, FAILED_DOWNLOAD_FIELDS, failed_rows)
    write_csv(validation_path, PDF_VALIDATION_FIELDS, validation_rows)

    invalid_pdf_count = sum(1 for row in validation_rows if row.get("status") != "ok")
    print(f"Master catalog: {master_catalog_path}")
    print(f"Download manifest: {manifest_path}")
    print(f"Status summary: {summary_path}")
    print(f"Failed downloads: {failed_path}")
    print(f"PDF validation: {validation_path}")
    print(f"Downloaded rows: {status_counts.get('downloaded', 0)}")
    print(f"Failed rows: {len(failed_rows)}")
    print(f"Invalid PDFs: {invalid_pdf_count}")
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "init-bib-run":
        return cmd_init_bib_run(args)
    if args.command == "import-bib":
        return cmd_import_bib(args)
    if args.command == "enrich-dois":
        return cmd_enrich_dois(args)
    if args.command == "build-queues":
        return cmd_build_queues(args)
    if args.command == "ingest-results":
        return cmd_ingest_results(args)
    if args.command == "finalize-run":
        return cmd_finalize_run(args)
    raise RuntimeError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
