import csv
import json
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

import db

enter_path_of_config_file = r"autotrader_config.csv"

# Used for parsing the specs text of each listing (not for search iteration)
_FUEL_TYPE_NAMES = [
    "Bi Fuel",
    "Diesel",
    "Diesel Hybrid",
    "Diesel Plug-in Hybrid",
    "Electric",
    "Petrol",
    "Petrol Hybrid",
    "Petrol Plug-in Hybrid",
    "Unlisted",
]

# Global Selenium instances — initialised in main()
driver: webdriver.Firefox  # type: ignore[assignment]
wait: WebDriverWait  # type: ignore[assignment]

# Extras price lookup — populated by _load_extras_prices() at startup.
# Outer key: "{Make}_{Model}" (spaces replaced with underscores), e.g. "Porsche_Taycan".
# Inner dict: canonical extra name → GBP list price.
EXTRAS_PRICES: dict[str, dict[str, int]] = {}
# Union of all canonical extra names across every model segment — gates the detail-page visit.
EXTRA_COLUMNS: list[str] = []

# ---------------------------------------------------------------------------
# Logging — INFO+ to console, DEBUG+ to scraper.log (handlers attached by _configure_logging)
# ---------------------------------------------------------------------------
log = logging.getLogger("autotrader")
log.setLevel(logging.DEBUG)


def _configure_logging() -> None:
    """Attach the console and ``scraper.log`` handlers to the ``autotrader`` logger (idempotent)."""
    if log.handlers:
        return
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))

    fh = logging.FileHandler("scraper.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    log.addHandler(ch)
    log.addHandler(fh)


# ---------------------------------------------------------------------------
# Extras helpers
# ---------------------------------------------------------------------------


def _load_extras_prices() -> None:
    """Populate EXTRAS_PRICES and EXTRA_COLUMNS from extras_prices.json via ``db.load_extras_prices``."""
    global EXTRAS_PRICES, EXTRA_COLUMNS
    EXTRAS_PRICES, EXTRA_COLUMNS = db.load_extras_prices()


def _model_key(make: str, model: str) -> str:
    """Return the extras_prices.json lookup key for *make* + *model*."""
    return f"{make}_{model}".replace(" ", "_")


def _get_model_extras(make: str, model: str) -> dict[str, int]:
    """Return the extras price table for *make*/*model*, or ``{}`` if not found."""
    key = _model_key(make, model)
    prices = EXTRAS_PRICES.get(key)
    if prices is None:
        log.info(
            "No extras pricing found for key %r — extras columns will be 0 for this model",
            key,
        )
        return {}
    log.debug("_get_model_extras: using segment %r (%d entries)", key, len(prices))
    return prices


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _match_extra(extra_text: str, active_prices: dict[str, int]) -> str | None:
    """Return the canonical name from *active_prices* that best matches *extra_text*.

    Pipeline:
    1. Exact match after normalisation.
    2. Keyword match — all words ≥4 chars from the canonical name must appear
       in the normalised extra text.
    """
    norm = _normalise(extra_text)
    for canonical in active_prices:
        if norm == _normalise(canonical):
            return canonical
    for canonical in active_prices:
        keywords = [w for w in _normalise(canonical).split() if len(w) >= 4]
        if keywords and all(kw in norm for kw in keywords):
            return canonical
    return None


# ---------------------------------------------------------------------------
# Selenium helpers
# ---------------------------------------------------------------------------


def scroll_to_load_all(pause: float = 1.5, max_scrolls: int = 30) -> int:
    """Scroll until the page height stabilises, triggering infinite-scroll loads.

    Returns the number of scrolls performed.
    """
    last_height = driver.execute_script("return document.body.scrollHeight")
    log.debug("scroll_to_load_all: initial page height %d", last_height)

    for scroll_n in range(1, max_scrolls + 1):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(pause)
        new_height = driver.execute_script("return document.body.scrollHeight")
        listing_count = len(driver.find_elements(By.CSS_SELECTOR, 'a[data-testid="search-listing-title"]'))
        log.debug(
            "scroll_to_load_all: scroll %d — height %d → %d, listings visible: %d",
            scroll_n,
            last_height,
            new_height,
            listing_count,
        )
        if new_height == last_height:
            log.debug("scroll_to_load_all: stable after %d scroll(s)", scroll_n)
            return scroll_n
        last_height = new_height

    log.warning(
        "scroll_to_load_all: reached max_scrolls=%d — page may still have unloaded content",
        max_scrolls,
    )
    return max_scrolls


def setup_cookies() -> None:
    """Set AutoTrader consent cookies to bypass the Sourcepoint CMP dialog.

    ``atwv=1`` prevents the CMP script from loading; ``acceptATCookies=true``
    treats consent as already given.  Both must be set after navigating to the
    domain at least once.
    """
    log.info("Setting consent cookies on autotrader.co.uk")
    driver.get("https://www.autotrader.co.uk")
    time.sleep(2)
    for name, value in [("acceptATCookies", "true"), ("atwv", "1")]:
        try:
            driver.add_cookie({"name": name, "value": value, "domain": ".autotrader.co.uk", "path": "/"})
            log.debug("Cookie set: %s=%s", name, value)
        except Exception as e:
            log.warning("Failed to set cookie %s: %s", name, e)
    log.info("Consent cookies set — reloading")
    driver.refresh()
    time.sleep(1)


# ---------------------------------------------------------------------------
# Detail-page extras scraper
# ---------------------------------------------------------------------------


def get_extras(detail_url: str, active_prices: dict[str, int]) -> tuple[dict[str, int], str]:
    """Navigate to *detail_url* and return matched extras with their list prices.

    Args:
        detail_url:    Full URL of the AutoTrader car advert page.
        active_prices: Extras price table for the current make/model, obtained
                       via ``_get_model_extras(make, model)``.  Only extras
                       present in this mapping are matched and priced; others
                       go to ``Extras_Other``.

    Returns:
        matched   — ``{canonical_name: list_price_gbp}`` for every extra found
                    in the "Added extras" section that appears in the price table.
        unmatched — comma-separated string of extras present on the car but not
                    in the price table (captured in ``Extras_Other`` column).
    """
    if not active_prices:
        return {}, ""

    matched: dict[str, int] = {}
    unmatched_list: list[str] = []

    try:
        log.debug("get_extras: navigating to %s", detail_url)
        driver.get(detail_url)

        try:
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "h1, [data-testid='advert-heading'], [data-testid='advert-title']"))
            )
        except Exception:
            log.warning("get_extras: page load timed out for %s", detail_url)
            return matched, ""

        time.sleep(1)

        # Log all data-testid values present — aids selector diagnosis
        page_soup = BeautifulSoup(driver.page_source, "html.parser")
        all_testids = sorted({str(el["data-testid"]) for el in page_soup.select("[data-testid]") if el.get("data-testid")})
        log.debug("get_extras: data-testid values on detail page: %s", all_testids)

        # --- Step 1: open "View all specs and features" panel ---
        # The confirmed data-testid from live pages is 'view-all-spec-and-features-signpost'
        view_specs_clicked = False

        for testid in [
            "view-all-spec-and-features-signpost",
            "view-all-specs",
            "specs-features-link",
            "all-specs-link",
            "view-specs",
        ]:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, f'[data-testid="{testid}"]')
                driver.execute_script("arguments[0].click();", btn)
                view_specs_clicked = True
                log.debug("get_extras: clicked [data-testid='%s']", testid)
                time.sleep(2)
                break
            except Exception:
                pass

        if not view_specs_clicked:
            phrases = ["view all specs", "specs and features", "view specs", "all specs"]
            for phrase in phrases:
                try:
                    xpath = f'//*[contains(translate(text(),"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"{phrase}")]'
                    btn = driver.find_element(By.XPATH, xpath)
                    driver.execute_script("arguments[0].click();", btn)
                    view_specs_clicked = True
                    log.debug("get_extras: clicked element with text %r", phrase)
                    time.sleep(2)
                    break
                except Exception:
                    pass

        if not view_specs_clicked:
            log.debug("get_extras: 'View all specs' button not found on %s", detail_url)
            return matched, ""

        # Log updated data-testid values after opening the specs panel
        page_soup = BeautifulSoup(driver.page_source, "html.parser")
        post_click_testids = sorted({str(el["data-testid"]) for el in page_soup.select("[data-testid]") if el.get("data-testid")})
        log.debug("get_extras: data-testid values after specs click: %s", post_click_testids)

        # --- Step 2: expand "Added extras" accordion ---
        extras_expanded = False
        for phrase in ["added extras", "optional extras", "factory options"]:
            try:
                xpath = f'//*[contains(translate(text(),"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"{phrase}")]'
                btn = driver.find_element(By.XPATH, xpath)
                driver.execute_script("arguments[0].click();", btn)
                extras_expanded = True
                log.debug("get_extras: expanded accordion with text %r", phrase)
                time.sleep(2)
                break
            except Exception:
                pass

        if not extras_expanded:
            log.debug("get_extras: 'Added extras' accordion not found on %s", detail_url)
            # Don't return early — some pages list extras without an accordion heading

        # --- Step 3: parse the expanded extras list ---
        page_soup = BeautifulSoup(driver.page_source, "html.parser")
        extras_texts: list[str] = []

        # Strategy A: find heading containing "added extras", walk ancestors for li items
        for heading_node in page_soup.find_all(string=re.compile(r"added extras", re.IGNORECASE)):
            parent = heading_node.find_parent()
            if parent is None:
                continue
            for ancestor in [parent] + list(parent.parents)[:6]:
                items = ancestor.select("li") or ancestor.select("[data-testid*='extra']")
                candidate = [i.get_text(strip=True) for i in items if i.get_text(strip=True)]
                if len(candidate) >= 2:  # avoid picking up single-item nav elements
                    extras_texts = candidate
                    log.debug("get_extras: found %d extras via heading ancestor search", len(extras_texts))
                    break
            if extras_texts:
                break

        # Strategy B: data-testid selectors for extras container
        if not extras_texts:
            for sel in [
                '[data-testid="added-extras"]',
                '[data-testid*="extras-list"]',
                '[data-testid*="optional-extras"]',
            ]:
                container = page_soup.select_one(sel)
                if container:
                    items = container.select("li") or container.find_all(True, recursive=False)
                    extras_texts = [i.get_text(strip=True) for i in items if i.get_text(strip=True)]
                    log.debug("get_extras: found %d extras via selector %r", len(extras_texts), sel)
                    break

        log.debug("get_extras: raw extras texts: %s", extras_texts)

        for raw_text in extras_texts:
            # AutoTrader appends "Added extra" (badge text) directly inside the same element.
            # Strip it so matching works on the actual extra name.
            text = re.sub(r"\s*Added\s+extra\s*$", "", raw_text, flags=re.IGNORECASE).strip()
            if not text:
                continue
            canonical = _match_extra(text, active_prices)
            if canonical:
                matched[canonical] = active_prices[canonical]
                log.debug("get_extras: matched %r → %r (£%d)", text, canonical, active_prices[canonical])
            else:
                unmatched_list.append(text)
                log.debug("get_extras: unmatched extra: %r", text)

        log.info(
            "get_extras: %d matched, %d unmatched — %s",
            len(matched),
            len(unmatched_list),
            detail_url,
        )

    except Exception as e:
        log.warning("get_extras: unexpected error for %s: %s", detail_url, e)

    return matched, ", ".join(unmatched_list)


def _title_matches(title: str, make: str, model: str) -> bool:
    """Return True if *title* contains both the make and model name (case-insensitive)."""
    lower = title.lower()
    if make and make.lower() not in lower:
        return False
    if model and model.lower() not in lower:
        return False
    return True


# ---------------------------------------------------------------------------
# Search-page scraping
# ---------------------------------------------------------------------------


def get_total_pages(
    postcode: str,
    make: str,
    model: str,
    trim: str,
    fuel: str,
    year_from: str,
    year_to: str,
    radius: str,
    price_to: str,
    page_start: int = 1,
) -> None:
    try:
        base_url = "https://www.autotrader.co.uk/car-search"
        encoded_postcode = quote_plus(postcode)
        encoded_make = quote_plus(make)
        encoded_model = quote_plus(model) if model else ""
        encoded_fuel = quote_plus(fuel)
        encoded_trim = quote_plus(trim) if trim else ""

        model_param = f"&model={encoded_model}" if encoded_model else ""
        trim_param = f"&aggregatedTrim={encoded_trim}" if encoded_trim else ""
        price_to_param = f"&price-to={quote_plus(price_to)}" if price_to else ""

        count_url = (
            f"{base_url}?fuel-type={encoded_fuel}&make={encoded_make}{model_param}{trim_param}"
            f"&postcode={encoded_postcode}&radius={radius}{price_to_param}"
            f"&year-from={year_from}&year-to={year_to}"
        )
        log.debug("Loading search URL: %s", count_url)
        driver.get(count_url)
        time.sleep(3)
        count_html = driver.page_source
        count_soup = BeautifulSoup(count_html, "html.parser")
        pagination_element = count_soup.find("p", {"data-testid": "pagination-show"})
        if pagination_element:
            pagination_text = pagination_element.text.strip()
            log.debug("Pagination text: %r", pagination_text)
            m = re.search(r"Page \d+ of (\d+)", pagination_text)
            if m:
                total_pages = min(101, int(m.group(1)))
            else:
                log.warning("Pagination element found but pattern did not match. Raw: %r", pagination_text)
                total_pages = 1
        else:
            log.warning(
                "Pagination element [data-testid='pagination-show'] not found — assuming 1 page. "
                "Check if the page loaded correctly or if the selector has changed. "
                "Current URL: %s",
                driver.current_url,
            )
            log.debug("Page HTML snippet (first 2000 chars):\n%s", count_html[:2000])
            total_pages = 1

        log.info(
            "Total pages for make=%r model=%r trim=%r fuel=%r: %d",
            make,
            model,
            trim,
            fuel,
            total_pages,
        )

        for page_number in range(page_start, total_pages + 1):
            try:
                rows: list[db.ListingRow] = []
                page_url = (
                    f"{base_url}?page={page_number}&fuel-type={encoded_fuel}&make={encoded_make}"
                    f"{model_param}{trim_param}&postcode={postcode}&radius={radius}"
                    f"{price_to_param}&year-from={year_from}&year-to={year_to}"
                )
                log.info("Fetching page %d / %d  — %s", page_number, total_pages, page_url)
                driver.get(page_url)

                try:
                    WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'a[data-testid="search-listing-title"]')))
                    log.debug("Listing cards rendered on page %d", page_number)
                except Exception:
                    log.warning(
                        "Timed out (60 s) waiting for listing cards on page %d. Current URL: %s — skipping page.",
                        page_number,
                        driver.current_url,
                    )
                    continue

                # Brief pause so React finishes hydrating the infinite-scroll container
                time.sleep(2)
                scrolls = scroll_to_load_all()
                log.info("Page %d: scrolling complete (%d scrolls)", page_number, scrolls)

                page_html = driver.page_source
                soup = BeautifulSoup(page_html, "html.parser")

                # Collect unique parent <li> elements for each listing anchor
                title_links = soup.select('a[data-testid="search-listing-title"]')
                seen: set[int] = set()
                li_elements = []
                for link in title_links:
                    parent_li = link.find_parent("li")
                    if parent_li and id(parent_li) not in seen:
                        seen.add(id(parent_li))
                        li_elements.append(parent_li)

                log.debug(
                    "Page %d: found %d listing cards via search-listing-title anchors",
                    page_number,
                    len(li_elements),
                )
                if not li_elements:
                    log.warning(
                        "Page %d: 0 listing cards found after full scroll. Page title: %r  Current URL: %s",
                        page_number,
                        soup.title.text if soup.title else "N/A",
                        driver.current_url,
                    )
                    log.debug("Page %d HTML snippet (first 3000 chars):\n%s", page_number, page_html[:3000])

                # --- Pass 1: extract all search-page data (no navigation) ---
                raw_listings: list[db.ListingRow] = []
                for idx, li_element in enumerate(li_elements):
                    title = get_title(li_element)
                    price_text = get_price(li_element)
                    year, miles, fuel_type = get_other_car_data(li_element, default_fuel=fuel)

                    if title is None:
                        log.warning(
                            "Page %d listing #%d: title not found. Li HTML: %s",
                            page_number,
                            idx,
                            li_element.prettify()[:500],
                        )
                    if price_text is None:
                        log.warning(
                            "Page %d listing #%d: price not found. Li HTML: %s",
                            page_number,
                            idx,
                            li_element.prettify()[:500],
                        )
                    if miles == "NA":
                        log.debug("Page %d listing #%d: miles not found", page_number, idx)

                    log.debug(
                        "Page %d listing #%d: title=%r price=%r year=%r miles=%r fuel=%r",
                        page_number,
                        idx,
                        title,
                        price_text,
                        year,
                        miles,
                        fuel_type,
                    )

                    # Build clean detail URL (strip search query params)
                    anchor = li_element.select_one('a[data-testid="search-listing-title"]')
                    href = anchor.get("href") if anchor else None
                    if href and isinstance(href, str) and href.startswith("/"):
                        detail_url: str | None = "https://www.autotrader.co.uk" + href.split("?")[0]
                    else:
                        detail_url = None

                    raw_listings.append(
                        {
                            "title": title,
                            "price": price_text,
                            "year": year,
                            "miles": miles,
                            "fuel_type": fuel_type,
                            "detail_url": detail_url,
                            "make": make,
                            "model": model or None,
                            "model_type": trim or None,
                            "postcode": postcode,
                            "extras": {},
                            "extras_other": "",
                        }
                    )

                # --- Filter: drop listings that don't match the expected make/model ---
                # AutoTrader occasionally returns unrelated makes in search results.
                # Filter before visiting detail pages to avoid wasting time.
                before_filter = len(raw_listings)
                raw_listings = [r for r in raw_listings if _title_matches(r["title"] or "", make, model)]
                skipped = before_filter - len(raw_listings)
                if skipped:
                    log.info(
                        "Page %d: filtered out %d listing(s) not matching make=%r model=%r",
                        page_number,
                        skipped,
                        make,
                        model,
                    )

                # Resolve the extras price table for this make/model once per page batch.
                active_prices = _get_model_extras(make, model)

                # --- Pass 2: visit each detail page and collect extras ---
                for raw in raw_listings:
                    d_url = raw["detail_url"]
                    if d_url and EXTRA_COLUMNS:
                        extras_matched, extras_other = get_extras(d_url, active_prices)
                        raw["extras"] = extras_matched
                        raw["extras_other"] = extras_other
                    rows.append(raw)

                log.info("Page %d: extracted %d raw listings, saving...", page_number, len(rows))
                result = db.save_listings(rows)
                log.info(
                    "Page %d: saved=%d skipped=%d attempts=%d succeeded=%s",
                    page_number,
                    result.saved,
                    result.skipped,
                    result.attempts,
                    result.succeeded,
                )

            except Exception as e:
                log.exception("Error processing page %d (%s): %s", page_number, page_url, e)
                db.append_error_log(f"Error processing page {page_number} ({page_url}): {type(e).__name__}: {e}")

    except Exception as e:
        log.exception("Fatal error in get_total_pages (make=%r model=%r fuel=%r): %s", make, model, fuel, e)


# ---------------------------------------------------------------------------
# Data extractors — search-page listing cards
# ---------------------------------------------------------------------------


def get_make_data() -> list[tuple[str, str, str, str, str]]:
    """Return ``[(uriValue, model, fuelType, aggregatedTrim, price_to)]`` from make.json.

    The list is reversed so that the last entry in the file is processed first
    (legacy behaviour — preserves run order from earlier versions).
    """
    with open("make.json", encoding="utf-8") as f:
        json_list = json.load(f)
    entries: list[tuple[str, str, str, str, str]] = [
        (
            entry["uriValue"],
            entry.get("model", ""),
            entry.get("fuelType", ""),
            entry.get("aggregatedTrim", ""),
            str(entry["price-to"]) if entry.get("price-to") else "",
        )
        for entry in json_list
    ][::-1]
    log.debug("get_make_data: loaded %d make/model entries", len(entries))
    return entries


def get_other_car_data(li_element: Tag, default_fuel: str = "NA") -> tuple[str, str, str]:
    """Extract year, mileage, and fuel type from a search-result listing card.

    Tries a structured specs container first; falls back to full-card text scan
    when AutoTrader changes its data-testid attributes.
    """
    year = miles = fuel_type = "NA"

    testids = [el.get("data-testid") for el in li_element.select("[data-testid]")]
    log.debug("get_other_car_data: data-testid attrs in card: %s", testids)

    specs_container = (
        li_element.select_one('[data-testid="search-listing-specs"]')
        or li_element.select_one('[data-testid*="spec"]')
        or li_element.select_one('[data-testid*="key-spec"]')
    )

    if specs_container:
        spec_items = specs_container.select("li") or specs_container.find_all(True, recursive=False)
        if spec_items:
            li_text_combined = " ".join(item.get_text(strip=True) for item in spec_items)
            log.debug("get_other_car_data: specs container text: %r", li_text_combined)
            year = spec_items[0].get_text(strip=True)
            miles_match = re.search(r"(\d[\d,]*\s*miles?)", li_text_combined, re.IGNORECASE)
            fuel_match = re.search(
                r"\b(" + "|".join(map(re.escape, _FUEL_TYPE_NAMES)) + r")\b",
                li_text_combined,
                re.IGNORECASE,
            )
            if miles_match:
                miles = miles_match.group(1)
            if fuel_match:
                fuel_type = fuel_match.group(1)
            if fuel_type == "NA" and default_fuel != "NA":
                fuel_type = default_fuel
            return year, miles, fuel_type

    # Fallback: regex scan of all text in the card
    full_text = li_element.get_text(separator=" ", strip=True)
    log.debug("get_other_car_data: full card text (first 400 chars): %r", full_text[:400])

    year_match = re.search(r"\b(20[1-3]\d)\b", full_text)
    if year_match:
        year = year_match.group(1)

    miles_match = re.search(r"(\d[\d,]*\s*miles?)", full_text, re.IGNORECASE)
    if miles_match:
        miles = miles_match.group(1)

    fuel_match = re.search(
        r"\b(" + "|".join(map(re.escape, _FUEL_TYPE_NAMES)) + r")\b",
        full_text,
        re.IGNORECASE,
    )
    if fuel_match:
        fuel_type = fuel_match.group(1)

    if fuel_type == "NA" and default_fuel != "NA":
        fuel_type = default_fuel

    if year == "NA" or miles == "NA":
        log.debug(
            "get_other_car_data: fallback result — year=%r miles=%r fuel=%r. Li HTML (first 1500 chars):\n%s",
            year,
            miles,
            fuel_type,
            li_element.prettify()[:1500],
        )

    return year, miles, fuel_type


def get_title(li_element: Tag) -> str | None:
    """Return the listing title from the search-listing-title anchor text."""
    anchor = li_element.select_one('a[data-testid="search-listing-title"]')
    if not anchor:
        log.debug("get_title: anchor a[data-testid='search-listing-title'] not found")
        return None
    text = anchor.get_text(strip=True)
    if not text:
        log.debug("get_title: anchor found but empty. Anchor HTML: %s", str(anchor)[:300])
        return None
    # AutoTrader appends the price to the anchor text (e.g. ", £39,995") — strip it.
    text = re.sub(r",\s*£[\d,]+\s*$", "", text).strip()
    return text if text else None


def get_price(li_element: Tag) -> str | None:
    """Return the listing price (digits only) from a search-result card."""
    # Try a specific price element — extract only the £ figure, not surrounding spec text
    price_el = li_element.select_one('[data-testid*="price"]')
    if price_el:
        m = re.search(r"£([\d,]+)", price_el.get_text(strip=True))
        if m:
            return m.group(1).replace(",", "")

    # Fallback: price is appended to the title anchor text as ", £XX,XXX"
    anchor = li_element.select_one('a[data-testid="search-listing-title"]')
    if anchor:
        m = re.search(r"£([\d,]+)\s*$", anchor.get_text(strip=True))
        if m:
            return m.group(1).replace(",", "")

    # Last resort: any text node matching £\d
    for el in li_element.find_all(string=re.compile(r"£\s*\d")):
        m = re.search(r"£([\d,]+)", str(el))
        if m:
            return m.group(1).replace(",", "")

    log.debug(
        "get_price: no price found. Candidate spans: %s",
        [str(s)[:80] for s in li_element.find_all("span") if "£" in s.get_text()],
    )
    return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchConfig:
    """One row of ``autotrader_config.csv``."""

    postcode: str
    page_number: int
    year_from: str
    year_to: str
    radius: str


def read_config(filename: str) -> list[SearchConfig]:
    """Read the search configuration CSV (UTF-8, with or without BOM)."""
    with open(filename, encoding="utf-8-sig", newline="") as f:
        return [
            SearchConfig(
                postcode=row["PostalCode"].strip(),
                page_number=int(row["PageNumber"]),
                year_from=row["year-from"].strip(),
                year_to=row["year-to"].strip(),
                radius=row["radius"].strip(),
            )
            for row in csv.DictReader(f)
        ]


def get_config(filename: str) -> None:
    log.info("Loading config from: %s", filename)
    configs = read_config(filename)
    log.info("Config rows: %d", len(configs))
    makes = get_make_data()
    log.info("Makes loaded: %d", len(makes))

    for row_num, config in enumerate(configs, start=1):
        for make, model, fuel_type, trim, price_to in makes:
            log.info(
                "Row %d — postcode=%r make=%r model=%r trim=%r fuel=%r years=%s–%s radius=%s price-to=%s start_page=%s",
                row_num,
                config.postcode,
                make,
                model or "(all)",
                trim or "(all)",
                fuel_type,
                config.year_from,
                config.year_to,
                config.radius,
                price_to or "(any)",
                config.page_number,
            )
            time.sleep(1)
            get_total_pages(
                config.postcode,
                make,
                model,
                trim,
                fuel_type,
                config.year_from,
                config.year_to,
                config.radius,
                price_to,
                config.page_number,
            )

    _log_scrape_summary()


def _log_scrape_summary() -> None:
    """Log the end-of-run row count; a failing count (e.g. dropped pooler) is only a WARNING."""
    total = None
    for attempt in (1, 2):
        try:
            db.database.connect(reuse_if_open=True)
            total = db.Listing.select().count()
            break
        except Exception as exc:
            if attempt == 1:
                try:
                    db.database.close()
                except Exception:
                    log.debug("close before summary reconnect failed", exc_info=True)
                continue
            log.warning("Scrape complete, but the final listing count failed: %s", type(exc).__name__)
            return
    log.info("Scrape complete. %d listing(s) in database", total)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    global driver, wait
    _configure_logging()
    load_dotenv()
    log.info("Starting AutoTrader scraper")
    try:
        db.init_db()
        _load_extras_prices()
        options = Options()
        # 'eager' fires after DOMContentLoaded — avoids hanging on ad/tracking resources
        options.page_load_strategy = "eager"
        driver = webdriver.Firefox(options=options)
        driver.maximize_window()
        wait = WebDriverWait(driver, 360)
        try:
            setup_cookies()
            get_config(enter_path_of_config_file)
        finally:
            log.info("Quitting browser")
            driver.quit()
    finally:
        db.close_db()
    log.info("DONE")


def cli() -> int:
    """Run ``main()`` and turn expected setup/database failures into a log line and exit code 1."""
    try:
        main()
    except (db.ConfigError, db.SchemaError, db.DatabaseUnavailableError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
