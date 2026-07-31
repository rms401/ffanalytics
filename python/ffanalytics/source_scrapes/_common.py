"""Shared plumbing for the site scrapers.

The R package uses ``rvest`` (libxml2) for HTML and ``httr2`` for JSON, with a
two-second delay between pages.  This module provides the same primitives on
``lxml`` -- also libxml2 -- so the CSS selectors and XPath expressions in
``R/source_scrapes.R`` carry over unchanged.
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Sequence

import pandas as pd
import requests
from lxml import html as lxml_html

from ..caching import cache_object, get_cached_object, is_cached, list_ffanalytics_cache
from ..rcompat.stats import type_convert_frame

__all__ = [
    "USER_AGENT",
    "Session",
    "html_table",
    "row_text",
    "rate_limit",
    "cached_positions",
    "store_scrape",
    "drop_all_na_columns",
    "convert_types",
]

USER_AGENT = (
    "ffanalytics R package (https://github.com/FantasyFootballAnalytics/ffanalytics)"
)

#: Seconds between page requests, matching the R package's Sys.sleep calls.
RATE_LIMIT_SECONDS = 2


class Session:
    """A keep-alive HTTP session that mirrors ``rvest::session``."""

    def __init__(self, base_url: str | None = None, timeout: int = 60) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.timeout = timeout
        if base_url:
            # Priming the session only sets cookies; a landing page that is
            # briefly unavailable should not sink the scrape that follows.
            try:
                self.get(base_url)
            except requests.RequestException:
                pass

    def get(self, url: str, **kwargs) -> requests.Response:
        response = self.session.get(url, timeout=self.timeout, **kwargs)
        response.raise_for_status()
        return response

    def read_html(self, url: str, **kwargs):
        """Fetch a page and parse it, as ``session_jump_to() |> read_html()``."""
        return lxml_html.fromstring(self.get(url, **kwargs).content)

    def get_json(self, url: str, **kwargs) -> Any:
        return self.get(url, **kwargs).json()

    def post_json(self, url: str, payload: dict, headers: dict | None = None) -> Any:
        response = self.session.post(
            url, json=payload, headers=headers or {}, timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()


def rate_limit(seconds: float = RATE_LIMIT_SECONDS) -> None:
    time.sleep(seconds)


_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "div", "dl", "dt", "dd",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
    "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
    "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}


def text2(node) -> str:
    """Approximate ``rvest::html_text2`` -- block elements start a new line.

    Several scrapers read column names out of a header cell that holds both a
    short label and a tooltip in a nested ``<div>``; ``html_text_content``
    would run them together, losing the split R relies on.
    """
    parts: list[str] = []

    def walk(element) -> None:
        if element.tag in _BLOCK_TAGS:
            parts.append("\n")
        if element.text:
            parts.append(element.text)
        for child in element:
            if isinstance(child.tag, str):
                walk(child)
            if child.tail:
                parts.append(child.tail)
        if element.tag in _BLOCK_TAGS:
            parts.append("\n")

    walk(node)
    text = "".join(parts)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def cell_texts(row, use_text2: bool = False) -> list[str]:
    """Text of each cell in a ``<tr>``."""
    cells = row.xpath("./td|./th")
    if use_text2:
        return [text2(cell) for cell in cells]
    return [_cell_text(cell) for cell in cells]


def _cell_text(cell) -> str:
    return " ".join(cell.text_content().split())


def html_table(table, header: bool | None = None, trim: bool = True) -> pd.DataFrame:
    """Parse a ``<table>`` (or ``<thead>``/``<tbody>``) into a frame of strings.

    Follows ``rvest::html_table``: cell text is taken raw and only *trimmed* at
    the ends, so runs of internal whitespace survive.  The CBS player column
    depends on that -- its regex splits on ``\\s{2,}`` boundaries that come from
    the markup's own indentation.  ``colspan`` repeats a cell across the columns
    it covers, and ``header=None`` treats the first row as column names only
    when it is made of ``<th>`` cells.
    """
    rows = []
    for row in table.xpath(".//tr"):
        cells = row.xpath("./td|./th")
        if not cells:
            continue
        values = []
        for cell in cells:
            text = cell.text_content()
            text = text.strip() if trim else text
            span = int(cell.get("colspan") or 1)
            values.extend([text] * max(span, 1))
        rows.append(values)

    if not rows:
        return pd.DataFrame()

    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]

    if header is None:
        first = table.xpath(".//tr")[0]
        header = len(first.xpath("./th")) > 0 and len(first.xpath("./td")) == 0

    if header:
        return pd.DataFrame(rows[1:], columns=rows[0])
    return pd.DataFrame(rows, columns=[f"X{i + 1}" for i in range(width)])


def row_text(node, separator: str = "\t", use_text2: bool = True) -> list[str]:
    """Cell text of each row joined by ``separator``.

    Stands in for ``html_text2()`` on a ``<thead>``, which is how the CBS and
    FantasyPros scrapers read their column names.  Accepts either a container
    or a single ``<tr>``.
    """
    rows = node.xpath(".//tr") or ([node] if node.tag == "tr" else [])
    return [separator.join(cell_texts(row, use_text2=use_text2)) for row in rows]


def cached_positions(display_name: str, file_name: str, positions: Sequence[str]):
    """Return the cached scrape when it covers every requested position.

    Mirrors the R scrapers: a cache that is missing a position is dropped
    rather than partially reused.
    """
    if not is_cached(display_name):
        return None

    cached = get_cached_object(file_name)
    if all(position.upper() in {key.upper() for key in cached} for position in positions):
        listing = list_ffanalytics_cache(quiet=True)
        elapsed = listing.loc[listing["object"] == display_name, "hr_min_since_cache"]
        age = elapsed.iloc[0] if len(elapsed) else "?"
        print(f"\nUsing the {display_name.replace(' Scrape', '')} scrape "
              f"that was cached {age} ago:")
        return {position: cached[position] for position in positions}

    from ..caching import clear_ffanalytics_cache

    clear_ffanalytics_cache(display_name)
    return None


def store_scrape(frames: dict, file_name: str) -> None:
    cache_object(frames, file_name)


def drop_all_na_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """``Filter(function(x) any(!is.na(x)), df)``."""
    keep = [column for column in frame.columns if frame[column].notna().any()]
    return frame[keep]


def convert_types(frame: pd.DataFrame, exclude: Iterable[str] = ("id", "src_id")) -> pd.DataFrame:
    """``type.convert(as.is = TRUE)`` over everything but the id columns."""
    return type_convert_frame(frame, exclude=tuple(exclude))
