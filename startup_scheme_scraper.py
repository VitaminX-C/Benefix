"""
Startup Ecosystem Scheme & Support Harvester
=============================================
A robust, production-grade web crawler designed to discover active business 
schemes, incubator/accelerator programs, grants, and funding opportunities 
from startup portals.

Requirements:
    pip install requests beautifulsoup4 pandas urllib3 playwright
    playwright install chromium  (optional: only if dynamic rendering is required)
"""

from dataclasses import dataclass, field
import datetime
import logging
from pathlib import Path
import sys
import time
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# Optional Playwright import with graceful fallback
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


# =====================================================================
# Logging Configuration
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("scraper_errors.log", mode="a", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)


# =====================================================================
# Data Structures
# =====================================================================
@dataclass
class SchemeItem:
    """Represents a single discovered scheme or opportunity."""
    name: str
    url: str
    organization: str
    description: str
    status: str
    date_added: str = field(
        default_factory=lambda: datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )


@dataclass
class PortalConfig:
    """Configuration target for a startup portal scraper."""
    name: str
    entry_url: str
    requires_js: bool = False
    max_pages: int = 5
    # Custom CSS/XPath selectors or keyword rules per target portal
    scheme_selector: str = "a"
    title_selector: str = ""
    desc_selector: str = ""


# =====================================================================
# Robot Parser & Politeness Engine
# =====================================================================
class RobotChecker:
    """Handles robots.txt checking and caching per domain."""

    def __init__(self, user_agent: str):
        self.user_agent = user_agent
        self._parsers: Dict[str, RobotFileParser] = {}

    def is_allowed(self, url: str) -> bool:
        """Verify if robots.txt allows scraping the given URL."""
        parsed = urlparse(url)
        domain_base = f"{parsed.scheme}://{parsed.netloc}"

        if domain_base not in self._parsers:
            robots_url = f"{domain_base}/robots.txt"
            rp = RobotFileParser()
            rp.set_url(robots_url)
            try:
                rp.read()
            except Exception as e:
                logger.warning(f"Could not fetch robots.txt from {robots_url}: {e}")
            self._parsers[domain_base] = rp

        return self._parsers[domain_base].can_fetch(self.user_agent, url)


# =====================================================================
# Main Scraper Class
# =====================================================================
class StartupSchemeScraper:
    """Main crawler and data aggregator engine."""

    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) StartupSchemeHarvester/1.0"
    CSV_FILENAME = "business_schemes.csv"
    CSV_COLUMNS = ["Scheme Name", "Scheme URL", "Organization", "Description", "Status", "Date Added"]

    # Keywords to evaluate if an item is active/open or expired/closed
    INACTIVE_KEYWORDS = {"closed", "expired", "archived", "ended", "discontinued", "inactive"}
    ACTIVE_KEYWORDS = {"active", "open", "apply now", "ongoing", "accepting"}

    def __init__(self, target_portals: List[PortalConfig], delay_seconds: float = 1.5):
        self.target_portals = target_portals
        self.delay = delay_seconds
        self.robot_checker = RobotChecker(self.USER_AGENT)
        self.session = self._build_http_session()

        # Metrics Tracking
        self.scanned_websites: Set[str] = set()
        self.total_found: int = 0
        self.new_added: int = 0
        self.duplicates_skipped: int = 0

    def _build_http_session(self) -> requests.Session:
        """Create a resilient requests session with automatic retries."""
        session = requests.Session()
        session.headers.update({
            "User-Agent": self.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })

        retries = Retry(
            total=3,
            backoff_factor=1,  # 1s, 2s, 4s wait
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False
        )
        adapter = HTTPAdapter(max_retries=retries)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def fetch_page_requests(self, url: str) -> Optional[str]:
        """Fetch static HTML using requests with retry & timeout logic."""
        try:
            time.sleep(self.delay)
            response = self.session.get(url, timeout=12)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            logger.error(f"HTTP Request failed for {url}: {e}")
            return None

    def fetch_page_playwright(self, url: str) -> Optional[str]:
        """Fetch dynamically rendered HTML using Playwright."""
        if not PLAYWRIGHT_AVAILABLE:
            logger.warning("Playwright requested but not installed. Falling back to HTTP requests.")
            return self.fetch_page_requests(url)

        try:
            time.sleep(self.delay)
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=self.USER_AGENT)
                page.goto(url, wait_until="networkidle", timeout=25000)
                content = page.content()
                browser.close()
                return content
        except Exception as e:
            logger.error(f"Playwright rendering failed for {url}: {e}")
            return None

    def is_active_status(self, text: str) -> str:
        """Determine scheme status (Active vs Expired) from page text context."""
        text_lower = text.lower()
        if any(kw in text_lower for kw in self.INACTIVE_KEYWORDS):
            return "Closed"
        if any(kw in text_lower for kw in self.ACTIVE_KEYWORDS):
            return "Active"
        return "Active"  # Default assumption for listed items unless flagged as closed

    def parse_generic_portal(self, config: PortalConfig, html: str, page_url: str) -> List[SchemeItem]:
        """Extract scheme details from HTML based on generic heuristics and selectors."""
        soup = BeautifulSoup(html, "html.parser")
        extracted_items: List[SchemeItem] = []

        elements = soup.select(config.scheme_selector)
        for el in elements:
            # Extract anchor link & name
            if el.name == "a":
                link_tag = el
            else:
                link_tag = el.find("a")

            if not link_tag or not link_tag.get("href"):
                continue

            href = link_tag["href"].strip()
            full_url = urljoin(page_url, href)

            # Skip self-referential links, anchors, or non-HTTP protocols
            if not full_url.startswith(("http://", "https://")) or full_url.strip("/") == page_url.strip("/"):
                continue

            # Title extraction
            if config.title_selector:
                title_el = el.select_one(config.title_selector)
                name = title_el.get_text(strip=True) if title_el else link_tag.get_text(strip=True)
            else:
                name = link_tag.get_text(strip=True)

            if not name or len(name) < 4:  # Filter noise like "More", "Link", "1", "2"
                continue

            # Description extraction
            description = "N/A"
            if config.desc_selector:
                desc_el = el.select_one(config.desc_selector)
                if desc_el:
                    description = desc_el.get_text(strip=True)
            else:
                # Heuristic: grab immediate parent text snippet if available
                parent_text = el.parent.get_text(" ", strip=True) if el.parent else ""
                if len(parent_text) > len(name):
                    description = parent_text[:250] + "..." if len(parent_text) > 250 else parent_text

            # Status determination
            container_text = el.get_text(strip=True) + " " + description
            status = self.is_active_status(container_text)

            if status == "Closed":
                continue  # Skip explicitly inactive/expired schemes

            extracted_items.append(SchemeItem(
                name=name,
                url=full_url,
                organization=config.name,
                description=description,
                status=status
            ))

        return extracted_items

    def process_portal(self, config: PortalConfig) -> List[SchemeItem]:
        """Orchestrate multi-page crawling for a given portal configuration."""
        logger.info(f"--- Starting Scan: {config.name} ---")
        discovered: List[SchemeItem] = []

        for page_num in range(1, config.max_pages + 1):
            target_url = config.entry_url.format(page=page_num) if "{page}" in config.entry_url else config.entry_url

            # Honor Robots.txt rules
            if not self.robot_checker.is_allowed(target_url):
                logger.warning(f"Robots.txt forbids crawling: {target_url}")
                break

            self.scanned_websites.add(target_url)

            # Fetch source
            if config.requires_js:
                html = self.fetch_page_playwright(target_url)
            else:
                html = self.fetch_page_requests(target_url)

            if not html:
                logger.error(f"Failed to fetch content from {target_url}. Skipping.")
                continue

            items = self.parse_generic_portal(config, html, target_url)
            discovered.extend(items)

            # Stop if the portal is non-paginated
            if "{page}" not in config.entry_url:
                break

        logger.info(f"Found {len(discovered)} potential scheme items on {config.name}")
        return discovered

    def update_csv_storage(self, new_items: List[SchemeItem]) -> Path:
        """Incrementally save or update business_schemes.csv without losing existing entries."""
        csv_path = Path(self.CSV_FILENAME).resolve()
        existing_urls: Set[str] = set()

        # Load existing records if available
        if csv_path.exists():
            try:
                df_existing = pd.read_csv(csv_path)
                if "Scheme URL" in df_existing.columns:
                    existing_urls = set(df_existing["Scheme URL"].dropna().str.strip())
            except Exception as e:
                logger.error(f"Error reading existing CSV ({csv_path}): {e}. Creating new dataset.")
                df_existing = pd.DataFrame(columns=self.CSV_COLUMNS)
        else:
            df_existing = pd.DataFrame(columns=self.CSV_COLUMNS)

        records_to_add = []
        for item in new_items:
            clean_url = item.url.strip()
            if clean_url in existing_urls:
                self.duplicates_skipped += 1
            else:
                existing_urls.add(clean_url)
                records_to_add.append({
                    "Scheme Name": item.name,
                    "Scheme URL": item.url,
                    "Organization": item.organization,
                    "Description": item.description,
                    "Status": item.status,
                    "Date Added": item.date_added
                })
                self.new_added += 1

        if records_to_add:
            df_new = pd.DataFrame(records_to_add)
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            df_combined.to_csv(csv_path, index=False, encoding="utf-8-sig")
            logger.info(f"Added {len(records_to_add)} new record(s) to {csv_path.name}")
        else:
            logger.info("No new unique schemes were found to append.")

        return csv_path

    def run(self) -> None:
        """Run the end-to-end harvesting process."""
        all_items: List[SchemeItem] = []

        for portal in self.target_portals:
            try:
                items = self.process_portal(portal)
                all_items.extend(items)
            except Exception as e:
                logger.error(f"Unhandled error processing portal '{portal.name}': {e}", exc_info=True)

        self.total_found = len(all_items)
        csv_filepath = self.update_csv_storage(all_items)

        # Final Summary Execution Report
        print("\n" + "=" * 50)
        print("         SCRAPING EXECUTION SUMMARY")
        print("=" * 50)
        print(f"Websites Scanned    : {len(self.scanned_websites)}")
        print(f"Schemes Found       : {self.total_found}")
        print(f"New Schemes Added   : {self.new_added}")
        print(f"Duplicates Skipped  : {self.duplicates_skipped}")
        print(f"CSV File Location   : {csv_filepath}")
        print("=" * 50 + "\n")


# =====================================================================
# Extensible Target Portals Configuration
# =====================================================================
PORTAL_TARGETS: List[PortalConfig] = [
    PortalConfig(
        name="Startup India Schemes",
        entry_url="https://www.startupindia.gov.in/content/sih/en/government-schemes.html",
        requires_js=False,
        scheme_selector="div.scheme-card, div.content-card, a.scheme-link, ul.schemes-list li",
        title_selector="h3, h4, .title",
        desc_selector="p, .description"
    ),
    PortalConfig(
        name="Ecosystem Opportunity Hub Example",
        entry_url="https://example-startup-hub.org/programs?page={page}",
        requires_js=False,
        max_pages=2,
        scheme_selector="article.program-card, div.opportunity-item",
        title_selector="h2.program-title",
        desc_selector="p.summary"
    ),
    # Easily add more startup portal URLs below:
    # PortalConfig(
    #     name="Dynamic Render Portal Example",
    #     entry_url="https://example-dynamic-portal.com/grants",
    #     requires_js=True,
    #     scheme_selector=".grant-row"
    # )
]


# =====================================================================
# Entrypoint
# =====================================================================
if __name__ == "__main__":
    scraper = StartupSchemeScraper(target_portals=PORTAL_TARGETS, delay_seconds=1.5)
    scraper.run()