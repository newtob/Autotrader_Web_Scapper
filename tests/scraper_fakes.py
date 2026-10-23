"""Fakes for driving ``auto_trader.get_total_pages`` without a browser.

Owned by ``tests/test_scraper_wiring.py``. Nothing here launches Firefox or touches the network.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit


def listing_card_html(
    title: str,
    href: str | None,
    price: str = "£42,000",
    year: str = "2021 (21 reg)",
    miles: str = "12,345 miles",
    fuel: str = "Electric",
) -> str:
    """Build one AutoTrader-style search-result ``<li>`` card.

    Args:
        title: Anchor text before the appended price.
        href: Anchor ``href``; ``None`` omits the attribute entirely.
        price: Display price such as ``"£42,000"``.
        year: First spec item text.
        miles: Mileage spec item text.
        fuel: Fuel spec item text.

    Returns:
        The card HTML.
    """
    href_attr = f' href="{href}"' if href is not None else ""
    return (
        "<li>"
        f'<a data-testid="search-listing-title"{href_attr}>{title}, {price}</a>'
        f'<span data-testid="search-listing-price">{price}</span>'
        '<ul data-testid="search-listing-specs">'
        f"<li>{year}</li><li>{miles}</li><li>{fuel}</li>"
        "</ul>"
        "</li>"
    )


def search_page_html(cards: list[str], total_pages: int = 1, current: int = 1) -> str:
    """Build a search-results page with pagination text and the given cards."""
    return (
        "<html><head><title>Search</title></head><body>"
        f'<p data-testid="pagination-show">Page {current} of {total_pages}</p>'
        f"<ul>{''.join(cards)}</ul>"
        "</body></html>"
    )


class FakeDriver:
    """Minimal Selenium driver stand-in that serves canned HTML.

    ``page_source`` is chosen from the ``page=`` query of the last URL passed to ``get``.
    A URL with no ``page=`` (the count URL) is served ``pages[1]`` unless ``count_html`` is set.
    Tests may mutate ``pages`` between runs.
    """

    def __init__(self, pages: dict[int, str], count_html: str | None = None) -> None:
        self.pages = pages
        self.count_html = count_html
        self.current_url = ""
        self.visited: list[str] = []

    def get(self, url: str) -> None:
        self.current_url = url
        self.visited.append(url)

    @property
    def page_source(self) -> str:
        query = parse_qs(urlsplit(self.current_url).query)
        if "page" not in query:
            return self.count_html if self.count_html is not None else self.pages[min(self.pages)]
        return self.pages[int(query["page"][0])]

    def execute_script(self, *args: Any, **kwargs: Any) -> int:
        return 0

    def find_elements(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def quit(self) -> None:
        pass


class FakeWait:
    """Stand-in for ``WebDriverWait`` whose ``until`` returns immediately."""

    def __init__(self, driver: Any, timeout: float = 0, *args: Any, **kwargs: Any) -> None:
        self.driver = driver
        self.timeout = timeout

    def until(self, method: Any, message: str = "") -> bool:
        return True
