"""
Read the text out of an approved knowledge document.

Supported: PDF, Word (.docx), plain text/Markdown, pasted text, questions and
answers, and a public web page.

Safety rules for web pages
  Only http and https. Every hop of a redirect is checked again. An address
  that resolves to a private, loopback, link-local or reserved network is
  refused, so the assistant can never be pointed at the school's own servers.
  Responses are capped at 10 MB and only HTML or plain text is accepted.
  A DNS answer can change between this check and the request itself; the
  request itself is still made by the HTTP library, so an internal address is
  never deliberately followed by us.

Nothing here raises a raw library error to the screen: every failure becomes
``ExtractionError`` with plain wording that never includes a file path.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit

from lxml import etree, html as lxml_html

MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
FETCH_TIMEOUT = 15.0
MAX_REDIRECTS = 3
USER_AGENT = "SchoolAssistantKnowledge/1.0 (knowledge base indexing)"
REDIRECT_CODES = {301, 302, 303, 307, 308}
HTML_TYPES = {"text/html", "application/xhtml+xml"}
TEXT_TYPES = {"text/plain"}


class ExtractionError(Exception):
    def __init__(self, message: str) -> None:
        self.safe_message = message
        super().__init__(message)


def extract_text(document) -> str:
    """Return the readable text for one document. Raises ``ExtractionError``."""
    source = document.source_type
    if source == document.SourceType.PDF:
        text = _extract_pdf(document)
    elif source == document.SourceType.DOCX:
        text = _extract_docx(document)
    elif source == document.SourceType.TEXT:
        text = _read_text_file(document)
    elif source == document.SourceType.PASTED:
        text = document.body_text or ""
    elif source == document.SourceType.FAQ:
        text = _extract_faq(document)
    elif source == document.SourceType.URL:
        text = fetch_url(document.source_url)
    else:
        raise ExtractionError("This kind of document cannot be read.")

    text = text.replace("\x00", "").strip()
    if not text:
        raise ExtractionError("This document has no readable text.")
    return text


# ── Files ────────────────────────────────────────────────────────────────────


def _extract_pdf(document) -> str:
    from pypdf import PdfReader

    if not document.file:
        raise ExtractionError("This document has no file to read.")
    try:
        with document.file.open("rb") as handle:
            reader = PdfReader(handle)
            if reader.is_encrypted:
                raise ExtractionError(
                    "This PDF is password-protected. Remove the password and upload it again."
                )
            pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except ExtractionError:
        raise
    except Exception:  # damaged, unreadable or unsupported PDF
        raise ExtractionError(
            "This PDF could not be read. It may be damaged or in an unsupported format."
        ) from None

    text = "\n\n".join(page for page in pages if page)
    if not text.strip():
        raise ExtractionError(
            "This PDF has no readable text. If it is a scan, upload a text version instead."
        )
    return text


def _extract_docx(document) -> str:
    from docx import Document as DocxDocument
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    if not document.file:
        raise ExtractionError("This document has no file to read.")
    try:
        docx_document = DocxDocument(document.file)
        parts: list[str] = []
        body = docx_document.element.body
        for child in body.iterchildren():
            if child.tag == qn("w:p"):
                paragraph = Paragraph(child, docx_document)
                text = paragraph.text.strip()
                if not text:
                    continue
                level = _heading_level(paragraph)
                parts.append(f"{'#' * level} {text}" if level else text)
            elif child.tag == qn("w:tbl"):
                rows = []
                for row in Table(child, docx_document).rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    if any(cells):
                        rows.append(" | ".join(cells))
                if rows:
                    parts.append("\n".join(rows))
    except Exception:
        raise ExtractionError(
            "This Word document could not be read. It may be damaged or not a .docx file."
        ) from None

    text = "\n\n".join(parts)
    if not text.strip():
        raise ExtractionError("This Word document has no readable text.")
    return text


def _heading_level(paragraph) -> int:
    style = getattr(paragraph, "style", None)
    name = (getattr(style, "name", "") or "").lower()
    match = re.match(r"heading\s*([1-6])", name)
    return int(match.group(1)) if match else 0


def _read_text_file(document) -> str:
    if not document.file:
        raise ExtractionError("This document has no file to read.")
    try:
        raw = document.file.read()
    except Exception:
        raise ExtractionError("This file could not be read.") from None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ExtractionError(
            "This text file is not saved as UTF-8. Save it again as UTF-8 and upload it."
        ) from None


def _extract_faq(document) -> str:
    parts: list[str] = []
    if (document.body_text or "").strip():
        parts.append(document.body_text.strip())
    for pair in document.qa_pairs.filter(is_active=True).order_by("display_order", "pk"):
        parts.append(f"Q: {pair.question.strip()}\nA: {pair.answer.strip()}")
    return "\n\n".join(parts)


# ── Web pages ────────────────────────────────────────────────────────────────


def fetch_url(url: str) -> str:
    """Fetch a public HTML or text page. Redirects and private hosts refused."""
    import httpx

    current = (url or "").strip()
    if not current:
        raise ExtractionError("This web page has no address.")

    with httpx.Client(follow_redirects=False, timeout=FETCH_TIMEOUT) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _check_public_address(current)
            try:
                with client.stream(
                    "GET",
                    current,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html,application/xhtml+xml,text/plain",
                    },
                ) as response:
                    if response.status_code in REDIRECT_CODES:
                        location = response.headers.get("location", "").strip()
                        if not location:
                            raise ExtractionError(
                                "The web page redirected without saying where to."
                            )
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        raise ExtractionError(
                            f"The web page could not be fetched (status {response.status_code})."
                        )
                    content_type = (
                        (response.headers.get("content-type") or "")
                        .split(";")[0]
                        .strip()
                        .lower()
                    )
                    if content_type not in HTML_TYPES | TEXT_TYPES:
                        raise ExtractionError(
                            "That address is not a web page. Add an HTML or plain-text page."
                        )
                    body = bytearray()
                    for piece in response.iter_bytes():
                        body.extend(piece)
                        if len(body) > MAX_DOWNLOAD_BYTES:
                            raise ExtractionError(
                                "The web page is larger than 10 MB. Add a smaller page."
                            )
                    encoding = response.encoding or "utf-8"
            except ExtractionError:
                raise
            except httpx.TimeoutException:
                raise ExtractionError("The web page took too long to answer.") from None
            except httpx.HTTPError:
                raise ExtractionError("The web page could not be reached.") from None

            text = bytes(body).decode(encoding, errors="replace")
            if content_type in HTML_TYPES:
                text = html_to_text(text)
            if not text.strip():
                raise ExtractionError("The web page has no readable text.")
            return text

    raise ExtractionError("The web page redirected too many times.")


def _check_public_address(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ExtractionError("Use an http:// or https:// address.")
    if not parts.hostname:
        raise ExtractionError("That web address is incomplete.")
    if parts.username or parts.password:
        raise ExtractionError("Remove the username and password from the address.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        answers = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ExtractionError("That web address could not be found.") from None
    for answer in answers:
        address = answer[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise ExtractionError("That web address could not be read.") from None
        if not ip.is_global:
            raise ExtractionError(
                "That address points to a private network, so it cannot be fetched."
            )


BLOCK_BREAK = "\u2028"
BLOCK_TAGS = (
    "p",
    "li",
    "tr",
    "div",
    "section",
    "article",
    "blockquote",
    "table",
    "ul",
    "ol",
    "pre",
)
REMOVED_TAGS = (
    "script",
    "style",
    "noscript",
    "nav",
    "header",
    "footer",
    "form",
    "svg",
    "iframe",
    "template",
)


def html_to_text(markup: str) -> str:
    """Keep readable text and heading structure; drop scripts and navigation."""
    try:
        root = lxml_html.fromstring(markup)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError):
        raise ExtractionError("The web page could not be read.") from None

    for tag in REMOVED_TAGS:
        for node in root.xpath(f"//{tag}"):
            node.drop_tree()

    for level in range(1, 7):
        for node in root.xpath(f"//h{level}"):
            heading = " ".join(node.text_content().split())
            node.clear()
            node.tag = "p"
            node.text = f"{'#' * level} {heading}".strip()

    for node in root.xpath("//br"):
        node.tail = BLOCK_BREAK + (node.tail or "")
    for tag in BLOCK_TAGS:
        for node in root.xpath(f"//{tag}"):
            node.tail = BLOCK_BREAK + (node.tail or "")

    raw = re.sub(r"[ \t\r\n\f\v]+", " ", root.text_content())
    lines = [line.strip() for line in raw.split(BLOCK_BREAK)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line for line in lines if line)).strip()
