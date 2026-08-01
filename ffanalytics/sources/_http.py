"""Fetching and parsing pages, shared by the site scrapers."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests
from lxml import html as lxml_html

__all__ = [
    "USER_AGENT",
    "Session",
    "html_table",
    "header_rows",
    "polite_pause",
    "for_each_position",
    "local_json",
    "local_html",
]

USER_AGENT = (
    "ffanalytics (https://github.com/FantasyFootballAnalytics/ffanalytics)"
)

#: Seconds between requests to the same site.
PAUSE_SECONDS = 2.0


class Session:
    """A keep-alive HTTP session with a browser-ish identity."""

    def __init__(self, prime_url: str | None = None, timeout: int = 60) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.timeout = timeout
        if prime_url:
            # Some sites only serve their tables once they have set a cookie.
            # A landing page that is briefly down should not sink the scrape.
            try:
                self.get(prime_url)
            except requests.RequestException:
                pass

    def get(self, url: str, **kwargs) -> requests.Response:
        response = self.session.get(url, timeout=self.timeout, **kwargs)
        response.raise_for_status()
        return response

    def html(self, url: str, **kwargs):
        return lxml_html.fromstring(self.get(url, **kwargs).content)

    def json(self, url: str, **kwargs):
        return self.get(url, **kwargs).json()

    def post_json(self, url: str, payload: dict, headers: dict | None = None):
        response = self.session.post(
            url, json=payload, headers=headers or {}, timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()


def polite_pause(seconds: float = PAUSE_SECONDS) -> None:
    time.sleep(seconds)


#: Directory (relative to the working directory) holding manually saved
#: copies of endpoints a site refuses to serve this machine.
LOCAL_DIR = Path("fdg")


def _local_file(*names) -> Path | None:
    """The first file in ``fdg/`` matching one of ``names``, if any.

    A file matches if its stem equals a name exactly (``qb.json``,
    ``adp.json``) or, for names longer than two characters, the name appears
    anywhere in the filename -- which covers browser-saved names like
    ``draft-guide-rankings-provider.php?POS=0.json``.
    """
    if not LOCAL_DIR.is_dir():
        return None
    wanted = [str(name).lower() for name in names]
    for path in sorted(LOCAL_DIR.iterdir()):
        if not path.is_file():
            continue
        stem, full = path.stem.lower(), path.name.lower()
        if any(stem == name or (len(name) > 2 and name in full)
               for name in wanted):
            return path
    return None


def local_json(*names) -> tuple[Path, object] | None:
    """A manually saved JSON copy of a blocked endpoint, if one exists.

    Some sites 403 requests that don't come from a browser (or from a
    particular network), but serve the same response happily elsewhere.
    Saving that response into ``fdg/`` in the working directory lets a
    scrape use it instead of the network.
    """
    path = _local_file(*names)
    if path is None:
        return None
    try:
        return path, json.loads(path.read_text())
    except ValueError as error:
        raise ValueError(
            f"{path} is not valid JSON -- save the endpoint's raw "
            "response body, not an HTML page"
        ) from error


def local_html(*names) -> tuple[Path, object] | None:
    """Like :func:`local_json`, for endpoints that serve HTML pages."""
    path = _local_file(*names)
    if path is None:
        return None
    return path, lxml_html.fromstring(path.read_bytes())


def html_table(table, header: bool | None = None) -> pd.DataFrame:
    """Parse a ``<table>`` (or a ``<thead>``/``<tbody>``) into text columns.

    ``colspan`` repeats a cell across the columns it covers, so grouped
    headers line up with the body.  Cell text is only trimmed at the ends:
    a couple of scrapers split the player cell on the runs of whitespace the
    markup's own indentation leaves behind.
    """
    rows = []
    for row in table.xpath(".//tr"):
        cells = row.xpath("./td|./th")
        if not cells:
            continue
        values = []
        for cell in cells:
            text = cell.text_content().strip()
            values.extend([text] * max(int(cell.get("colspan") or 1), 1))
        rows.append(values)

    if not rows:
        return pd.DataFrame()

    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]

    if header is None:
        first = table.xpath(".//tr")[0]
        header = bool(first.xpath("./th")) and not first.xpath("./td")

    if header:
        return pd.DataFrame(rows[1:], columns=rows[0])
    return pd.DataFrame(rows, columns=[f"X{i + 1}" for i in range(width)])


_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "div", "dl", "dt", "dd",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
    "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
})


def header_rows(node, separator: str = "\t") -> list[str]:
    """Each row's cell text, joined by ``separator``.

    Block elements inside a cell start a new line first.  Several sites put a
    short label and a tooltip in the same header cell, and running them
    together would lose the column name.
    """
    rows = node.xpath(".//tr") or ([node] if node.tag == "tr" else [])
    return [
        separator.join(_block_text(cell) for cell in row.xpath("./td|./th"))
        for row in rows
    ]


def _block_text(element) -> str:
    parts: list[str] = []

    def walk(node) -> None:
        if node.tag in _BLOCK_TAGS:
            parts.append("\n")
        if node.text:
            parts.append(node.text)
        for child in node:
            if isinstance(child.tag, str):
                walk(child)
            if child.tail:
                parts.append(child.tail)
        if node.tag in _BLOCK_TAGS:
            parts.append("\n")

    walk(element)
    lines = (" ".join(line.split()) for line in "".join(parts).split("\n"))
    return "\n".join(line for line in lines if line)


def for_each_position(positions, scrape_one):
    """Run ``scrape_one`` per position, reporting failures instead of raising.

    One position's markup changing should not cost you the other eight.
    """
    out = {}
    for position in positions:
        try:
            frame = scrape_one(position)
        except Exception as error:  # noqa: BLE001 - one bad page must not abort the rest
            print(f"  ! {position}: {type(error).__name__}: {error}")
            continue
        if frame is not None and len(frame):
            out[position] = frame.reset_index(drop=True)
    return out
