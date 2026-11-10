"""
apps/learning/markdown_utils.py
---------------------------------
Safe Markdown → HTML rendering for lesson notes.

The output is sanitised with bleach so that:
  - Teachers who accidentally (or intentionally) include <script> tags,
    event handlers, iframes or any other executable content can never
    inject malicious HTML into a student's browser.
  - Only a well-defined allow-list of tags and attributes reaches the page.
  - The result is marked django.utils.safestring.mark_safe so Django
    templates do not double-escape it.

Allowed HTML (chosen to cover all Markdown's standard output plus images):
  Tags:   p, br, h1-h4, strong, em, ul, ol, li, blockquote,
          code, pre, a, img, hr, table, thead, tbody, tr, th, td
  Attrs:  a[href|title|rel], img[src|alt|title|width|height]

Everything else is stripped — not escaped — so the content is still readable.
Scripts, iframes, on* event attributes, and style attributes are removed.
Image src is restricted to http/https to prevent data-URI payloads.
"""

from __future__ import annotations

import markdown
import bleach
from bleach.linkifier import LinkifyFilter
from django.utils.safestring import mark_safe


# Tags that Markdown legitimately produces
ALLOWED_TAGS = [
    "p", "br",
    "h1", "h2", "h3", "h4",
    "strong", "em", "del",
    "ul", "ol", "li",
    "blockquote",
    "code", "pre",
    "a",
    "img",
    "hr",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
]

ALLOWED_ATTRS = {
    "a":   ["href", "title", "rel"],
    "img": ["src", "alt", "title", "width", "height"],
    "th":  ["align"],
    "td":  ["align"],
}

# Markdown extensions to enable
MD_EXTENSIONS = [
    "tables",        # | col | col | table syntax
    "fenced_code",   # ```python ... ``` code blocks
    "sane_lists",    # saner nested list behaviour
    "attr_list",     # adds support for {.class} attributes on headings
]


def render_notes(text: str, *, notes_format: str = "plain") -> str:
    """
    Convert *text* to safe HTML for display in a lesson page.

    - ``notes_format="plain"``    → white-space preserved, no Markdown parsing,
                                     HTML-escaped and line-breaks converted to <br>.
    - ``notes_format="markdown"`` → full Markdown → sanitised HTML.

    Returns a ``SafeString`` ready for ``{{ value }}`` in a Django template
    without an extra ``|safe`` filter needed (it is already marked safe).
    """
    if not text:
        return mark_safe("")

    if notes_format == "markdown":
        # 1. Convert Markdown to HTML
        raw_html = markdown.markdown(
            text,
            extensions=MD_EXTENSIONS,
            output_format="html",
        )
    else:
        # Plain text: escape HTML characters, then turn newlines into <br>
        from django.utils.html import escape, linebreaks
        return mark_safe(linebreaks(escape(text)))

    # 2. Sanitise with bleach — strip disallowed tags and attributes
    clean = bleach.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        strip=True,       # strip (not escape) disallowed tags
        strip_comments=True,
    )

    # 3. Ensure all <a> links open in a new tab with rel="noopener noreferrer"
    #    (protects students from tab-napping via teacher-inserted links)
    clean = _patch_links(clean)

    return mark_safe(clean)


def _patch_links(html: str) -> str:
    """Add target="_blank" rel="noopener noreferrer" to every <a> tag."""
    from html.parser import HTMLParser
    import re

    # Simple regex approach — safe because bleach already cleaned the HTML
    def replace_a(m):
        tag = m.group(0)
        if 'target=' not in tag:
            tag = tag.rstrip('>') + ' target="_blank" rel="noopener noreferrer">'
        return tag

    return re.sub(r'<a\s[^>]*>', replace_a, html)
