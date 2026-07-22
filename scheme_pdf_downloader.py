#!/usr/bin/env python3
"""
Scheme PDF Downloader
======================

Reads one or more CSV files that contain a "Scheme URL" column, visits each
URL, crawls the page (and, optionally, one level of linked pages on the same
site) looking for links to PDF files, and downloads every PDF it finds into a
local "Scheme_PDFs" folder.

Usage
-----
    pip install requests beautifulsoup4 tqdm

    python scheme_pdf_downloader.py student_schemes.csv business_schemes.csv

    # Options
    python scheme_pdf_downloader.py *.csv --output Scheme_PDFs --depth 1 --workers 8

Notes
-----
- Each scheme gets its own sub-folder inside Scheme_PDFs (named after the
  "Scheme Name" column) so downloads from different schemes never collide.
- A run log / summary CSV is written to Scheme_PDFs/_download_log.csv so you
  can see what succeeded, what failed, and why.
- The script is polite: it uses a real User-Agent, a timeout on every
  request, and skips a domain for the rest of the run if it starts timing
  out repeatedly (simple circuit breaker) rather than hammering a dead site.
- --depth 1 (default 0) additionally follows same-domain links found on the
  scheme page one level deep, in case PDFs live on a "Downloads" or
  "Notifications" sub-page rather than the landing page itself.
"""

import argparse
import concurrent.futures
import csv
import os
import re
import sys
import time
import urllib.parse
from collections import defaultdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:
    from tqdm import tqdm
except ImportError:  # tqdm is optional, fall back to no-op
    def tqdm(iterable=None, **kwargs):
        return iterable if iterable is not None else []


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 SchemePDFBot/1.0"
    )
}
REQUEST_TIMEOUT = 20          # seconds per HTTP request
MAX_FAILS_PER_DOMAIN = 3      # circuit breaker threshold
PDF_LINK_RE = re.compile(r"\.pdf($|\?)", re.IGNORECASE)


def safe_filename(name: str, maxlen: int = 120) -> str:
    """Turn an arbitrary string into a filesystem-safe name."""
    name = re.sub(r"[^\w\-. ]+", "_", name).strip(" ._") or "untitled"
    return name[:maxlen]


def read_scheme_rows(csv_paths):
    """Yield (scheme_name, url, source_file) for every usable row across all CSVs."""
    seen_urls = set()
    for path in csv_paths:
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                url = (row.get("Scheme URL") or "").strip()
                name = (row.get("Scheme Name") or "").strip() or "Untitled Scheme"
                if not url.lower().startswith(("http://", "https://")):
                    continue
                key = url.rstrip("/")
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                yield name, url, os.path.basename(path)


class DomainCircuitBreaker:
    """Stops hitting a domain after too many consecutive failures."""

    def __init__(self, max_fails=MAX_FAILS_PER_DOMAIN):
        self.max_fails = max_fails
        self.fail_counts = defaultdict(int)
        self.tripped = set()

    def domain_of(self, url):
        return urllib.parse.urlparse(url).netloc

    def is_open(self, url):
        return self.domain_of(url) in self.tripped

    def record_failure(self, url):
        d = self.domain_of(url)
        self.fail_counts[d] += 1
        if self.fail_counts[d] >= self.max_fails:
            self.tripped.add(d)

    def record_success(self, url):
        d = self.domain_of(url)
        self.fail_counts[d] = 0


def fetch(session, url, breaker):
    """GET a URL, respecting the circuit breaker. Returns Response or None."""
    if breaker.is_open(url):
        return None
    try:
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if resp.status_code >= 400:
            breaker.record_failure(url)
            return None
        breaker.record_success(url)
        return resp
    except requests.RequestException:
        breaker.record_failure(url)
        return None


def find_pdf_links(html, base_url):
    """Return a set of absolute PDF URLs found in an HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    links = set()
    for tag in soup.find_all(["a", "iframe", "embed"]):
        href = tag.get("href") or tag.get("src")
        if not href:
            continue
        href = href.strip()
        if PDF_LINK_RE.search(href):
            links.add(urllib.parse.urljoin(base_url, href))
    return links


def find_same_domain_page_links(html, base_url, limit=25):
    """Return a small set of same-domain non-PDF links to explore one level deeper."""
    soup = BeautifulSoup(html, "html.parser")
    base_domain = urllib.parse.urlparse(base_url).netloc
    out = set()
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if PDF_LINK_RE.search(href):
            continue
        abs_url = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(abs_url)
        if parsed.netloc != base_domain:
            continue
        if parsed.scheme not in ("http", "https"):
            continue
        # Skip mailto/tel/anchors already filtered by urljoin; skip obvious junk
        if any(x in href.lower() for x in ("javascript:", "#", "mailto:", "tel:")):
            continue
        out.add(abs_url)
        if len(out) >= limit:
            break
    return out


def download_pdf(session, url, dest_folder, breaker):
    """Download a single PDF. Returns (success: bool, filepath_or_reason: str)."""
    if breaker.is_open(url):
        return False, "domain circuit-breaker tripped"
    try:
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True)
        if resp.status_code >= 400:
            breaker.record_failure(url)
            return False, f"HTTP {resp.status_code}"

        parsed_name = os.path.basename(urllib.parse.urlparse(url).path) or "file.pdf"
        if not parsed_name.lower().endswith(".pdf"):
            parsed_name += ".pdf"
        filename = safe_filename(parsed_name)
        dest_path = Path(dest_folder) / filename

        # avoid overwriting a different file that happens to share a name
        counter = 1
        while dest_path.exists():
            dest_path = Path(dest_folder) / f"{Path(filename).stem}_{counter}{Path(filename).suffix}"
            counter += 1

        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        breaker.record_success(url)
        return True, str(dest_path)
    except requests.RequestException as e:
        breaker.record_failure(url)
        return False, str(e)


def process_scheme(scheme_name, url, output_root, depth, breaker, session):
    """
    Visit a scheme's page (and optionally one level of linked pages),
    find PDF links, download them into their own sub-folder.
    Returns a list of log-row dicts.
    """
    log_rows = []
    folder = Path(output_root) / safe_filename(scheme_name)

    resp = fetch(session, url, breaker)
    if resp is None:
        log_rows.append({
            "scheme_name": scheme_name, "page_url": url, "pdf_url": "",
            "status": "FAILED_TO_LOAD_PAGE", "saved_path": "",
        })
        return log_rows

    pdf_links = find_pdf_links(resp.text, url)

    if depth >= 1:
        sub_pages = find_same_domain_page_links(resp.text, url)
        for sub_url in sub_pages:
            sub_resp = fetch(session, sub_url, breaker)
            if sub_resp is None:
                continue
            pdf_links |= find_pdf_links(sub_resp.text, sub_url)

    if not pdf_links:
        log_rows.append({
            "scheme_name": scheme_name, "page_url": url, "pdf_url": "",
            "status": "NO_PDF_FOUND", "saved_path": "",
        })
        return log_rows

    folder.mkdir(parents=True, exist_ok=True)
    for pdf_url in sorted(pdf_links):
        ok, info = download_pdf(session, pdf_url, folder, breaker)
        log_rows.append({
            "scheme_name": scheme_name, "page_url": url, "pdf_url": pdf_url,
            "status": "DOWNLOADED" if ok else f"DOWNLOAD_FAILED: {info}",
            "saved_path": info if ok else "",
        })
    return log_rows


def main():
    parser = argparse.ArgumentParser(description="Download PDF links found on scheme web pages listed in CSV files.")
    parser.add_argument("csv_files", nargs="+", help="One or more CSV files with a 'Scheme URL' column")
    parser.add_argument("--output", default="Scheme_PDFs", help="Output folder (default: Scheme_PDFs)")
    parser.add_argument("--depth", type=int, default=0, choices=[0, 1],
                         help="0 = only scan the given URL; 1 = also scan same-domain linked pages one level deep (default: 0)")
    parser.add_argument("--workers", type=int, default=6, help="Number of schemes to process in parallel (default: 6)")
    args = parser.parse_args()

    for f in args.csv_files:
        if not os.path.isfile(f):
            print(f"ERROR: file not found: {f}", file=sys.stderr)
            sys.exit(1)

    rows = list(read_scheme_rows(args.csv_files))
    print(f"Loaded {len(rows)} unique scheme URLs from {len(args.csv_files)} CSV file(s).")

    output_root = Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)

    breaker = DomainCircuitBreaker()
    session = requests.Session()

    all_log_rows = []
    start = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(process_scheme, name, url, output_root, args.depth, breaker, session): (name, url)
            for name, url, _src in rows
        }
        for future in tqdm(concurrent.futures.as_completed(futures), total=len(futures), desc="Scanning schemes"):
            name, url = futures[future]
            try:
                all_log_rows.extend(future.result())
            except Exception as e:
                all_log_rows.append({
                    "scheme_name": name, "page_url": url, "pdf_url": "",
                    "status": f"UNEXPECTED_ERROR: {e}", "saved_path": "",
                })

    # Write summary log
    log_path = output_root / "_download_log.csv"
    with open(log_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["scheme_name", "page_url", "pdf_url", "status", "saved_path"])
        writer.writeheader()
        writer.writerows(all_log_rows)

    downloaded = sum(1 for r in all_log_rows if r["status"] == "DOWNLOADED")
    no_pdf = sum(1 for r in all_log_rows if r["status"] == "NO_PDF_FOUND")
    failed_load = sum(1 for r in all_log_rows if r["status"] == "FAILED_TO_LOAD_PAGE")
    failed_dl = sum(1 for r in all_log_rows if r["status"].startswith("DOWNLOAD_FAILED"))

    elapsed = time.time() - start
    print("\n----- Summary -----")
    print(f"Schemes scanned:       {len(rows)}")
    print(f"PDFs downloaded:       {downloaded}")
    print(f"Pages with no PDF:     {no_pdf}")
    print(f"Pages that failed to load: {failed_load}")
    print(f"PDF downloads that failed: {failed_dl}")
    print(f"Time elapsed:          {elapsed:.1f}s")
    print(f"Files saved under:     {output_root.resolve()}")
    print(f"Full log:              {log_path.resolve()}")


if __name__ == "__main__":
    main()
