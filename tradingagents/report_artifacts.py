"""Renderer-neutral validation for accepted user-report artifacts.

The canonical final-state builder owns business acceptance.  Presentation
layers may translate its Markdown to HTML/PDF, but they may not reinterpret
facts.  These helpers keep the Markdown dialect and the rendered-HTML checks
shared between that acceptance boundary and the web renderer.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import markdown

MARKDOWN_EXTENSIONS = ("tables", "fenced_code", "sane_lists", "nl2br")

_RAW_MARKDOWN = re.compile(
    r"(?m)^\s*(?:#{1,6}\s+\S|[-*_]{3,}\s*$|\|[^\n]*\|\s*$)"
)


def render_markdown_fragment(text: str) -> str:
    """Render accepted Markdown with the exact dialect used by the web UI."""
    return markdown.markdown(text, extensions=list(MARKDOWN_EXTENSIONS))


class _RenderedArtifactParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.visible: list[str] = []
        self.local_urls: list[str] = []
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"style", "script"}:
            self.skip_depth += 1
            return
        for name, value in attrs:
            if name.lower() in {"href", "src"} and str(value or "").lower().startswith(
                "file://"
            ):
                self.local_urls.append(str(value))
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"style", "script"}:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        self.visible.append(data)
        if self._cell is not None:
            self._cell.append(data)


def validate_rendered_html(html_text: str) -> list[str]:
    """Return structural issues visible after Markdown-to-HTML conversion."""
    parser = _RenderedArtifactParser()
    parser.feed(html_text)
    issues: list[str] = []
    if parser.local_urls:
        issues.append("LOCAL_FILE_URL_VISIBLE")
    visible = "\n".join(parser.visible)
    if _RAW_MARKDOWN.search(visible):
        issues.append("RAW_MARKDOWN_VISIBLE_AFTER_RENDER")
    for table in parser.tables:
        widths = [len(row) for row in table if row]
        if not widths or len(set(widths)) != 1:
            issues.append("MALFORMED_RENDERED_TABLE")
            continue
        if any(_RAW_MARKDOWN.search(cell) for row in table for cell in row):
            issues.append("MARKDOWN_SWALLOWED_BY_TABLE")
    return list(dict.fromkeys(issues))
