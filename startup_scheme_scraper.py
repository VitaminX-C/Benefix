#!/usr/bin/env python3
"""
Production-Quality Startup Ecosystem & Government Scheme Crawler Engine (v2.0)
-------------------------------------------------------------------------------
Improvements in v2:
- Multi-depth internal domain crawler with link discovery.
- URL keyword filtering to avoid noise (login, news, careers, etc.).
- Enhanced semantic parser for Eligibility, Benefits, and Last Updated dates.
- Fully backwards compatible with existing CSV storage and NetworkEngine.
"""

from dataclasses import dataclass, asdict, fields
import logging
import os
from pathlib import Path
import random
import re
import time
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import bs4
from bs4 import BeautifulSoup
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
LOG_FILENAME = "scraper_errors.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILENAME, mode="a", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("EcosystemCrawler")

# Key terminology for URL filtering and content verification
RELEVANT_KEYWORDS = {
    "scheme", "grant", "fund", "funding", "startup", "support",
    "program", "programme", "incubator", "accelerator", "innovation",
    "challenge", "loan", "subsidy", "seed"
}

IGNORED_URL_TOKENS = {
    "login", "signin", "signup", "contact", "privacy", "terms",
    "career", "careers", "job", "jobs", "news", "media", "press",
    "faq", "faqs", "sitemap", "gallery", "event", "events"
}


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
@dataclass
class SchemeRecord:
    """Represents a standardized business support scheme record."""
    scheme_name: str
    url: str
    organization: str
    category: str
    description: str
    eligibility: Optional[str] = "N/A"
    benefits: Optional[str] = "N/A"
    last_updated: Optional[str] = "N/A"

    def clean(self) -> "SchemeRecord":
        """Clean whitespace and standardize text fields."""
        def _clean_str(val: Optional[str]) -> str:
            if not val:
                return "N/A"
            text = " ".join(val.split())
            return text if text else "N/A"

        return SchemeRecord(
            scheme_name=_clean_str(self.scheme_name),
            url=self.url.strip(),
            organization=_clean_str(self.organization),
            category=_clean_str(self.category),
            description=_clean_str(self.description),
            eligibility=_clean_str(self.eligibility),
            benefits=_clean_str(self.benefits),
            last_updated=_clean_str(self.last_updated)
        )


# ---------------------------------------------------------------------------
# Target Portals
# ---------------------------------------------------------------------------
TARGET_PORTALS = [
    {
        "name": "Startup India",
        "start_url": "https://www.startupindia.gov.in/content/sih/en/government-schemes.html",
        "category": "Central Government Scheme",
        "use_playwright": False
    },
    {
        "name": "Kerala Startup Mission (KSUM)",
        "start_url": "https://startupmission.kerala.gov.in/schemes",
        "category": "State Startup Mission",
        "use_playwright": False
    },
    {
        "name": "Atal Innovation Mission (AIM)",
        "start_url": "https://aim.gov.in/",
        "category": "Incubation & Innovation Grant",
        "use_playwright": False
    },
    {
        "name": "MSME Champions Portal",
        "start_url": "https://champions.gov.in/",
        "category": "MSME Scheme",
        "use_playwright": False
    }
]


# ---------------------------------------------------------------------------
# Robust HTTP Fetcher & Politeness Engine
# ---------------------------------------------------------------------------
class NetworkEngine:
    """Handles HTTP requests with retries, exponential backoff, and robots.txt compliance."""

    def __init__(self, request_delay: float = 1.0, max_retries: int = 3, timeout: int = 15):
        self.request_delay = request_delay
        self.timeout = timeout
        self.session = requests.Session()
        self.robot_parsers: Dict[str, RobotFileParser] = {}

        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        })

        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def is_allowed_by_robots(self, url: str) -> bool:
        """Parse and respect robots.txt rules for the given target domain."""
        parsed = urlparse(url)
        domain_base = f"{parsed.scheme}://{parsed.netloc}"
        
        if domain_base not in self.robot_parsers:
            robots_url = urljoin(domain_base, "/robots.txt")
            parser = RobotFileParser()
            parser.set_url(robots_url)
            try:
                parser.read()
            except Exception as err:
                logger.warning(f"Could not fetch robots.txt for {domain_base}: {err}")
            self.robot_parsers[domain_base] = parser

        return self.robot_parsers[domain_base].can_fetch(self.session.headers["User-Agent"], url)

    def fetch(self, url: str, use_playwright: bool = False) -> Optional[str]:
        """Fetch raw HTML content using Requests or Playwright fallback."""
        if not self.is_allowed_by_robots(url):
            logger.warning(f"Skipping disallowed path by robots.txt: {url}")
            return None

        time.sleep(self.request_delay + random.uniform(0.1, 0.4))

        if use_playwright:
            return self._fetch_playwright(url)
        
        return self._fetch_requests(url)

    def _fetch_requests(self, url: str) -> Optional[str]:
        try:
            response = self.session.get(url, timeout=self.timeout, verify=True)
            response.raise_for_status()
            return response.text
        except requests.exceptions.SSLError:
            try:
                response = self.session.get(url, timeout=self.timeout, verify=False)
                return response.text
            except Exception as e:
                logger.error(f"Failed to fetch {url} on SSL retry: {e}")
                return None
        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP error fetching {url}: {e}")
            return None

    def _fetch_playwright(self, url: str) -> Optional[str]:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=30000)
                html = page.content()
                browser.close()
                return html
        except Exception as e:
            logger.error(f"Playwright rendering failed for {url}: {e}")
            return None


# ---------------------------------------------------------------------------
# High-Recall HTML Extractor & Semantic Parser
# ---------------------------------------------------------------------------
class SchemeParser:
    """Extracts links and structured scheme fields using semantic heuristics."""

    @staticmethod
    def extract_internal_links(base_url: str, html: str) -> Set[str]:
        """Discover valid, relevant internal links on the page."""
        soup = BeautifulSoup(html, "html.parser")
        parsed_base = urlparse(base_url)
        discovered_urls: Set[str] = set()

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            
            # Skip non-navigational links
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            if any(href.lower().endswith(ext) for ext in [".pdf", ".png", ".jpg", ".zip", ".docx"]):
                continue

            full_url = urljoin(base_url, href)
            parsed_full = urlparse(full_url)

            # Restrict strictly to the same domain
            if parsed_full.netloc != parsed_base.netloc:
                continue

            # Check if URL passes relevance keyword filter and lacks noise
            if SchemeParser._is_relevant_url(full_url):
                discovered_urls.add(full_url)

        return discovered_urls

    @staticmethod
    def _is_relevant_url(url: str) -> bool:
        """Determines if a candidate link is worth visiting based on path keywords."""
        path_lower = urlparse(url).path.lower()
        
        # Banned keyword check
        if any(token in path_lower for token in IGNORED_URL_TOKENS):
            return False

        # Positive keyword check or shallow path heuristic
        if any(keyword in path_lower for keyword in RELEVANT_KEYWORDS):
            return True
            
        return len(path_lower.strip("/").split("/")) <= 2

    @staticmethod
    def parse_page(url: str, html: str, default_org: str, default_cat: str) -> Optional[SchemeRecord]:
        """Evaluates if a page represents a scheme and extracts structured details."""
        soup = BeautifulSoup(html, "html.parser")
        text_content = soup.get_text(" ", strip=True).lower()

        # Score page relevance: requires multiple matching domain terms
        matches = sum(1 for kw in RELEVANT_KEYWORDS if kw in text_content)
        if matches < 2:
            return None

        # Extract Scheme Name
        title_elem = soup.find(["h1", "h2"])
        if title_elem and len(title_elem.get_text(strip=True)) > 5:
            scheme_name = title_elem.get_text(strip=True)
        elif soup.title:
            scheme_name = soup.title.get_text(strip=True).split("|")[0].split("-")[0]
        else:
            return None

        # Extract Description
        meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
        if meta_desc and meta_desc.get("content"):
            description = meta_desc["content"]
        else:
            paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 40]
            description = " ".join(paragraphs[:2]) if paragraphs else "Official government support scheme."

        # Section-specific Parsers
        eligibility = SchemeParser._extract_section_by_keyword(soup, ["eligibility", "who can apply", "eligible"])
        benefits = SchemeParser._extract_section_by_keyword(soup, ["benefits", "assistance", "financial support", "incentive"])
        last_updated = SchemeParser._extract_date(soup)

        return SchemeRecord(
            scheme_name=scheme_name,
            url=url,
            organization=default_org,
            category=default_cat,
            description=description,
            eligibility=eligibility,
            benefits=benefits,
            last_updated=last_updated
        ).clean()

    @staticmethod
    def _extract_section_by_keyword(soup: BeautifulSoup, keywords: List[str]) -> str:
        """Finds paragraph/list blocks following target section headers."""
        for kw in keywords:
            header = soup.find(lambda tag: tag.name in ["h2", "h3", "h4", "strong", "b"] and kw in tag.get_text().lower())
            if header:
                # Get next dynamic sibling elements
                siblings = []
                curr = header.next_sibling
                while curr and len(siblings) < 3:
                    if isinstance(curr, bs4.element.Tag):
                        if curr.name in ["h2", "h3"]:  # Stop at next section
                            break
                        text = curr.get_text(strip=True)
                        if text:
                            siblings.append(text)
                    curr = curr.next_sibling
                if siblings:
                    return " ".join(siblings)[:400]
        return "N/A"

    @staticmethod
    def _extract_date(soup: BeautifulSoup) -> str:
        """Finds last updated date signatures on the webpage."""
        date_pattern = re.compile(r"(last updated|updated on|date):?\s*([0-9]{1,2}[-/\s][0-9]{1,2}[-/\s][0-9]{2,4}|[a-zA-Z]+\s+[0-9]{1,2},\s+[0-9]{4})", re.I)
        match = date_pattern.search(soup.get_text())
        if match:
            return match.group(2)
        
        time_tag = soup.find("time")
        if time_tag and time_tag.get_text():
            return time_tag.get_text(strip=True)

        return "N/A"


# ---------------------------------------------------------------------------
# Storage & Persistence Controller
# ---------------------------------------------------------------------------
class DataStorageManager:
    """Handles DataFrame processing, deduplication, and safe CSV persistence."""

    def __init__(self, csv_filepath: str = "business_schemes.csv"):
        self.filepath = Path(csv_filepath)
        self.expected_columns = [f.name for f in fields(SchemeRecord)]

    def initialize_csv_if_missing(self) -> None:
        """Create empty CSV with correct schema if it doesn't exist."""
        if not self.filepath.exists():
            df = pd.DataFrame(columns=self.expected_columns)
            df.to_csv(self.filepath, index=False, encoding="utf-8")
            logger.info(f"Created new CSV file: {self.filepath.resolve()}")

    def load_existing_urls(self) -> Set[str]:
        """Extract existing URLs from CSV to maintain state."""
        self.initialize_csv_if_missing()
        try:
            df = pd.read_csv(self.filepath)
            if "url" in df.columns:
                return set(df["url"].dropna().astype(str).str.strip().tolist())
        except Exception as e:
            logger.error(f"Error reading existing CSV: {e}")
        return set()

    def append_new_records(self, records: List[SchemeRecord]) -> Tuple[int, int]:
        """Deduplicate records against existing CSV entries and persist updates."""
        self.initialize_csv_if_missing()
        existing_urls = self.load_existing_urls()

        new_records: List[Dict] = []
        skipped_count = 0

        for rec in records:
            cleaned_rec = rec.clean()
            if cleaned_rec.url in existing_urls:
                skipped_count += 1
            else:
                new_records.append(asdict(cleaned_rec))
                existing_urls.add(cleaned_rec.url)

        if new_records:
            new_df = pd.DataFrame(new_records, columns=self.expected_columns)
            new_df.to_csv(self.filepath, mode="a", header=False, index=False, encoding="utf-8")
            logger.info(f"Appended {len(new_records)} new records to {self.filepath}")

        return len(new_records), skipped_count


# ---------------------------------------------------------------------------
# Main Multi-Depth Crawler Orchestrator
# ---------------------------------------------------------------------------
class StartupEcosystemCrawler:
    """Orchestrates recursive discovery across official portals."""

    def __init__(self, output_csv: str = "business_schemes.csv", crawl_depth: int = 1):
        self.crawl_depth = crawl_depth
        self.network = NetworkEngine(request_delay=1.0)
        self.storage = DataStorageManager(csv_filepath=output_csv)
        self.pages_crawled = 0
        self.websites_scanned = 0

    def run(self) -> None:
        """Runs the multi-depth crawler across all configured portal targets."""
        logger.info(f"Starting Multi-Depth Startup Portal Scraper (Max Depth: {self.crawl_depth})...")
        
        all_discovered_schemes: List[SchemeRecord] = []

        for portal in TARGET_PORTALS:
            self.websites_scanned += 1
            logger.info(f"Scanning portal [{self.websites_scanned}/{len(TARGET_PORTALS)}]: {portal['name']}")
            
            portal_schemes = self._crawl_portal(portal)
            all_discovered_schemes.extend(portal_schemes)

        # Append to CSV and preserve deduplication state
        new_added, duplicates_skipped = self.storage.append_new_records(all_discovered_schemes)

        # Print Summary Report
        self._print_summary(
            schemes_found=len(all_discovered_schemes),
            new_added=new_added,
            duplicates_skipped=duplicates_skipped
        )

    def _crawl_portal(self, portal: Dict) -> List[SchemeRecord]:
        """Crawl a target portal using depth-limited queue traversal."""
        start_url = portal["start_url"]
        org_name = portal["name"]
        category = portal["category"]
        use_pw = portal.get("use_playwright", False)

        queue: List[Tuple[str, int]] = [(start_url, 0)]
        visited: Set[str] = set()
        portal_schemes: List[SchemeRecord] = []

        while queue:
            current_url, depth = queue.pop(0)

            if current_url in visited or depth > self.crawl_depth:
                continue

            visited.add(current_url)
            html_content = self.network.fetch(current_url, use_playwright=use_pw)
            
            if not html_content:
                continue

            self.pages_crawled += 1

            # Extract structured scheme if page qualifies
            record = SchemeParser.parse_page(
                url=current_url,
                html=html_content,
                default_org=org_name,
                default_cat=category
            )
            if record:
                portal_schemes.append(record)

            # Discover next links if within depth limit
            if depth < self.crawl_depth:
                internal_links = SchemeParser.extract_internal_links(current_url, html_content)
                for link in internal_links:
                    if link not in visited:
                        queue.append((link, depth + 1))

        return portal_schemes

    def _print_summary(self, schemes_found: int, new_added: int, duplicates_skipped: int) -> None:
        """Outputs summary to standard output."""
        summary = f"""
==================================================
           CRAWLER EXECUTION SUMMARY             
==================================================
Websites Scanned          : {self.websites_scanned}
Pages Crawled             : {self.pages_crawled}
Schemes Found             : {schemes_found}
New Schemes Added         : {new_added}
Duplicate Schemes Skipped : {duplicates_skipped}
CSV File Location         : {self.storage.filepath.resolve()}
==================================================
        """
        print(summary)


# ---------------------------------------------------------------------------
# Script Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Crawl seed pages + level 1 internal subpages
    crawler = StartupEcosystemCrawler(output_csv="business_schemes.csv", crawl_depth=1)
    crawler.run()