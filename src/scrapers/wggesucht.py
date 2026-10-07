"""
WG-Gesucht scraper for Berlin 1-room apartments.

Fetches 1-Zimmer-Wohnungen from WG-Gesucht and converts them
to the common Listing format used by the apartment notifier.

WG rooms are intentionally excluded.
"""

import logging
import re
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from src.core.listing import Listing
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class WGGesuchtScraper(BaseScraper):
    """Scraper for Berlin 1-Zimmer-Wohnungen on WG-Gesucht."""

    supports_early_termination = False

    def __init__(self, name: str):
        super().__init__(name)

        self.base_url = "https://www.wg-gesucht.de"

        # Berlin, 1-Zimmer-Wohnungen
        self.url = (
            "https://www.wg-gesucht.de/"
            "1-zimmer-wohnungen-in-Berlin.8.1.1.0.html"
        )

        self.headers.update(
            {
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,image/avif,"
                    "image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
                "Referer": "https://www.wg-gesucht.de/",
                "DNT": "1",
                "Upgrade-Insecure-Requests": "1",
            }
        )

    def _fetch_raw_items(self) -> list:
        """
        Fetch the current Berlin 1-room apartment search page.

        Returns a list of apartment URLs.
        """
        response = requests.get(
            self.url,
            headers=self.headers,
            timeout=(10, 30),
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")

        urls = []
        seen = set()

        # WG-Gesucht apartment detail URLs contain a numeric ad ID.
        pattern = re.compile(
            r"(?:1-zimmer-wohnungen-in-Berlin\.)?(\d{6,})\.html"
        )

        for link in soup.find_all("a", href=True):
            href = link.get("href", "")

            if not pattern.search(href):
                continue

            full_url = urljoin(self.base_url, href)
            full_url = full_url.split("?")[0]

            if full_url in seen:
                continue

            seen.add(full_url)
            urls.append(full_url)

        logger.debug(
            "WG-Gesucht: found %s candidate apartment URLs",
            len(urls),
        )

        # First page is enough for frequent GitHub Actions checks.
        return urls[:50]

    def _extract_identifier_fast(self, raw_item) -> Optional[str]:
        """The raw item is already the apartment URL."""
        if isinstance(raw_item, str):
            return raw_item
        return None

    def _parse_item(self, url: str) -> Optional[Listing]:
        """Fetch and parse an individual WG-Gesucht apartment."""

        try:
            response = requests.get(
                url,
                headers=self.headers,
                timeout=(10, 30),
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning(
                "WG-Gesucht detail page failed %s: %s",
                url,
                exc,
            )
            return None

        soup = BeautifulSoup(response.text, "lxml")

        text = soup.get_text(" ", strip=True)

        # Safety check:
        # WG rooms are occasionally incorrectly placed in the apartment
        # category. Exclude obvious WG-room advertisements.
        title = self._extract_title(soup)
        title_lower = title.lower()

        wg_room_terms = (
            "wg-zimmer",
            "wg zimmer",
            "zimmer in wg",
            "zimmer in einer wg",
            "wg room",
            "room in shared",
            "shared room",
        )

        if any(term in title_lower for term in wg_room_terms):
            logger.debug(
                "WG-Gesucht: skipped probable WG room: %s",
                title,
            )
            return None

        price_total = self._extract_total_rent(text)
        sqm = self._extract_sqm(text)
        address = self._extract_address(soup, text)
        borough = self._extract_borough(address)

        # This scraper intentionally searches the
        # 1-Zimmer-Wohnung category.
        rooms = "1"

        return Listing(
            source=self.name,
            address=address,
            borough=borough,
            sqm=sqm,
            price_cold="N/A",
            price_total=price_total,
            rooms=rooms,
            wbs=None,
            identifier=url,
        )

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> str:
        """Extract listing title."""

        h1 = soup.find("h1")

        if h1:
            return h1.get_text(" ", strip=True)

        if soup.title:
            return soup.title.get_text(" ", strip=True)

        return "N/A"

    def _extract_total_rent(self, text: str) -> str:
        """
        Extract Gesamtmiete.

        WG-Gesucht detail pages normally expose a value such as:
        Gesamtmiete 580€
        """

        patterns = (
            r"Gesamtmiete\s*:?\s*([\d\.,]+)\s*€",
            r"Gesamtmiete\s*:?\s*€?\s*([\d\.,]+)",
        )

        for pattern in patterns:
            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:
                value = match.group(1)
                return self._normalize_price(value)

        return "N/A"

    def _extract_sqm(self, text: str) -> str:
        """Extract apartment size."""

        patterns = (
            r"Größe\s*:?\s*([\d\.,]+)\s*m²",
            r"([\d\.,]+)\s*m²",
        )

        for pattern in patterns:
            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:
                return self._normalize_price(match.group(1))

        return "N/A"

    def _extract_address(
        self,
        soup: BeautifulSoup,
        text: str,
    ) -> str:
        """Extract address from the detail page."""

        # First try structured/address-looking blocks.
        for element in soup.find_all(
            ["div", "span", "p", "h2", "h3"]
        ):
            candidate = element.get_text(" ", strip=True)

            if re.search(
                r"\b1\d{4}\s+Berlin\b",
                candidate,
                flags=re.IGNORECASE,
            ):
                candidate = re.sub(
                    r"^Adresse\s*:?\s*",
                    "",
                    candidate,
                    flags=re.IGNORECASE,
                )

                if len(candidate) <= 200:
                    return candidate

        # Fallback: find text around a Berlin postcode.
        match = re.search(
            r"Adresse\s+(.{0,120}?\b1\d{4}\s+Berlin"
            r"(?:\s+[A-Za-zÄÖÜäöüß\-\s]+)?)",
            text,
            flags=re.IGNORECASE,
        )

        if match:
            return re.sub(
                r"\s+",
                " ",
                match.group(1),
            ).strip()

        return "Berlin"

    def _extract_borough(self, address: str) -> str:
        """Determine Berlin borough using the postcode resolver."""

        match = re.search(r"\b(1\d{4})\b", address)

        if not match:
            return "N/A"

        return self._get_borough_from_zip(match.group(1))

    @staticmethod
    def _normalize_price(value: str) -> str:
        """
        Normalize German formatted numeric values.

        Examples:
        580       -> 580
        580,50    -> 580.50
        1.250,00  -> 1250.00
        """

        if not value:
            return "N/A"

        value = value.strip()

        if "," in value:
            value = value.replace(".", "")
            value = value.replace(",", ".")

        elif value.count(".") == 1:
            left, right = value.split(".")

            # German thousands notation: 1.200
            if len(right) == 3:
                value = left + right

        return value
