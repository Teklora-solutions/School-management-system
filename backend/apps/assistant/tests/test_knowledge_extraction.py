"""
Reading a document: extraction from each source type, web-page safety and the
chunk boundaries. No test here calls a real network or a real provider.
"""

from __future__ import annotations

import io
import socket
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from apps.assistant.knowledge import chunking, extraction
from apps.assistant.knowledge.extraction import ExtractionError

from .knowledge_helpers import KnowledgeTestCase


def text_pdf_bytes(text: str = "Fees are due each term.") -> bytes:
    """A tiny but valid one-page PDF with real text (no external library)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def docx_bytes(paragraphs: list[tuple[str, str | None]], table: list[list[str]] | None = None) -> bytes:
    from docx import Document

    document = Document()
    for text, style in paragraphs:
        document.add_paragraph(text, style=style)
    if table:
        rows = len(table)
        columns = len(table[0])
        built = document.add_table(rows=rows, cols=columns)
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                built.cell(r, c).text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class FileExtractionTests(KnowledgeTestCase):
    def test_pasted_text_is_returned(self):
        document = self.make_document(
            self.make_collection("Fees"), title="Policy", body_text="Fees are due each term."
        )
        self.assertEqual(extraction.extract_text(document), "Fees are due each term.")

    def test_pdf_text_is_read(self):
        document = self.make_document(
            self.make_collection("Fees"),
            source_type="pdf",
            body_text="",
            file=SimpleUploadedFile("fees.pdf", text_pdf_bytes("Fees are due each term.")),
        )
        self.assertIn("Fees are due each term.", extraction.extract_text(document))

    def test_a_scanned_pdf_is_refused_with_plain_wording(self):
        from pypdf import PdfWriter

        buffer = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.write(buffer)
        document = self.make_document(
            self.make_collection("Scans"),
            source_type="pdf",
            body_text="",
            file=SimpleUploadedFile("scan.pdf", buffer.getvalue()),
        )
        with self.assertRaises(ExtractionError) as caught:
            extraction.extract_text(document)
        self.assertIn("scan", str(caught.exception))

    def test_a_damaged_pdf_is_refused(self):
        document = self.make_document(
            self.make_collection("Broken"),
            source_type="pdf",
            body_text="",
            file=SimpleUploadedFile("broken.pdf", b"%PDF-1.4\nthis is not a pdf"),
        )
        with self.assertRaises(ExtractionError):
            extraction.extract_text(document)

    def test_docx_keeps_headings_and_table_rows(self):
        data = docx_bytes(
            [("Fees", "Heading 1"), ("Fees are due each term.", None), ("Payment plans", "Heading 2")],
            table=[["Term", "Amount"], ["One", "1000"]],
        )
        document = self.make_document(
            self.make_collection("Word"),
            source_type="docx",
            body_text="",
            file=SimpleUploadedFile("policy.docx", data),
        )
        text = extraction.extract_text(document)
        self.assertIn("# Fees", text)
        self.assertIn("## Payment plans", text)
        self.assertIn("Term | Amount", text)
        self.assertIn("One | 1000", text)

    def test_an_empty_docx_is_refused(self):
        document = self.make_document(
            self.make_collection("Empty word"),
            source_type="docx",
            body_text="",
            file=SimpleUploadedFile("empty.docx", docx_bytes([])),
        )
        with self.assertRaises(ExtractionError):
            extraction.extract_text(document)

    def test_a_utf8_text_file_is_read(self):
        document = self.make_document(
            self.make_collection("Notes"),
            source_type="txt",
            body_text="",
            file=SimpleUploadedFile("notes.txt", "Fees are due each term.".encode("utf-8")),
        )
        self.assertEqual(extraction.extract_text(document), "Fees are due each term.")

    def test_a_text_file_that_is_not_utf8_is_refused(self):
        document = self.make_document(
            self.make_collection("Legacy"),
            source_type="txt",
            body_text="",
            file=SimpleUploadedFile("notes.txt", b"\xff\xfe\x00F\x00e"),
        )
        with self.assertRaises(ExtractionError) as caught:
            extraction.extract_text(document)
        self.assertIn("UTF-8", str(caught.exception))

    def test_faq_extraction_uses_the_introduction_and_active_pairs(self):
        collection = self.make_collection("Questions")
        document = self.make_document(
            collection,
            title="FAQ",
            source_type="faq",
            body_text="Common questions.",
        )
        document.qa_pairs.create(question="When are fees due?", answer="Each term.", display_order=1)
        document.qa_pairs.create(
            question="Old question", answer="Old answer", display_order=2, is_active=False
        )
        text = extraction.extract_text(document)
        self.assertIn("Common questions.", text)
        self.assertIn("Q: When are fees due?", text)
        self.assertIn("A: Each term.", text)
        self.assertNotIn("Old question", text)


class HtmlExtractionTests(SimpleTestCase):
    def test_headings_are_kept_and_navigation_and_scripts_dropped(self):
        text = extraction.html_to_text(
            "<html><body><nav>Menu</nav><h1>Fees</h1>"
            "<p>Fees are <b>due</b>.</p><script>alert(1)</script>"
            "<ul><li>First</li><li>Second</li></ul>"
            "<h2>Plans</h2><p>Pay monthly.</p></body></html>"
        )
        self.assertIn("# Fees", text)
        self.assertIn("## Plans", text)
        self.assertIn("Fees are due.", text)
        self.assertIn("First", text)
        self.assertNotIn("Menu", text)
        self.assertNotIn("alert", text)

    def test_extra_whitespace_is_collapsed(self):
        text = extraction.html_to_text("<p>Fees   are\n\n due.</p>")
        self.assertEqual(text, "Fees are due.")

    def test_malformed_markup_is_still_readable(self):
        text = extraction.html_to_text("<p>Fees are due.<p>Pay monthly.")
        self.assertIn("Fees are due.", text)

    def test_a_blank_page_has_no_text(self):
        self.assertEqual(extraction.html_to_text("<html><body> </body></html>"), "")


def public_dns(host, port, **kwargs):
    addresses = {
        "example.com": "93.184.216.34",
        "www.example.org": "93.184.216.35",
        "localhost": "127.0.0.1",
        "internal.school": "10.0.0.8",
        "link.school": "169.254.10.2",
    }
    address = addresses.get(host, host)
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))]


class _Response:
    def __init__(self, status=200, headers=None, body=b"", encoding="utf-8"):
        self.status_code = status
        self.headers = headers or {}
        self.body = body
        self.encoding = encoding

    def iter_bytes(self):
        yield self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requested = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def stream(self, method, url, headers=None):
        self.requested.append(url)
        return self.responses.pop(0)


class UrlSafetyTests(SimpleTestCase):
    def assert_refused(self, url):
        with patch.object(extraction.socket, "getaddrinfo", side_effect=public_dns):
            with self.assertRaises(ExtractionError):
                extraction._check_public_address(url)

    def test_a_public_https_address_is_allowed(self):
        with patch.object(extraction.socket, "getaddrinfo", side_effect=public_dns):
            extraction._check_public_address("https://example.com/fees")

    def test_loopback_is_refused(self):
        self.assert_refused("http://127.0.0.1/admin")

    def test_a_private_network_is_refused(self):
        self.assert_refused("http://internal.school/fees")

    def test_a_link_local_address_is_refused(self):
        self.assert_refused("http://link.school/fees")

    def test_ftp_is_refused(self):
        self.assert_refused("ftp://example.com/fees.pdf")

    def test_credentials_in_the_address_are_refused(self):
        self.assert_refused("https://user:secret@example.com/fees")

    def test_an_unknown_host_is_refused(self):
        with patch.object(extraction.socket, "getaddrinfo", side_effect=socket.gaierror):
            with self.assertRaises(ExtractionError) as caught:
                extraction._check_public_address("https://missing.example/fees")
        self.assertIn("could not be found", str(caught.exception))


class UrlFetchTests(SimpleTestCase):
    def fetch(self, responses, url="https://example.com/fees"):
        client = _Client(responses)
        with patch.object(extraction.socket, "getaddrinfo", side_effect=public_dns), patch(
            "httpx.Client", return_value=client
        ):
            text = extraction.fetch_url(url)
        return text, client

    def test_html_is_fetched_and_cleaned(self):
        text, client = self.fetch(
            [
                _Response(
                    headers={"content-type": "text/html; charset=utf-8"},
                    body=b"<h1>Fees</h1><p>Fees are due.</p>",
                )
            ]
        )
        self.assertIn("# Fees", text)
        self.assertIn("Fees are due.", text)
        self.assertEqual(client.requested, ["https://example.com/fees"])

    def test_plain_text_is_accepted(self):
        text, _ = self.fetch(
            [_Response(headers={"content-type": "text/plain"}, body=b"Fees are due each term.")]
        )
        self.assertEqual(text, "Fees are due each term.")

    def test_one_redirect_is_followed(self):
        text, client = self.fetch(
            [
                _Response(status=302, headers={"location": "https://www.example.org/fees"}),
                _Response(
                    headers={"content-type": "text/html"},
                    body=b"<p>Fees are due.</p>",
                ),
            ]
        )
        self.assertIn("Fees are due.", text)
        self.assertEqual(len(client.requested), 2)

    def test_a_redirect_to_a_private_address_is_refused(self):
        client = _Client(
            [
                _Response(status=302, headers={"location": "http://127.0.0.1/secret"}),
            ]
        )
        with patch.object(extraction.socket, "getaddrinfo", side_effect=public_dns), patch(
            "httpx.Client", return_value=client
        ):
            with self.assertRaises(ExtractionError) as caught:
                extraction.fetch_url("https://example.com/fees")
        self.assertIn("private", str(caught.exception))
        self.assertEqual(len(client.requested), 1)

    def test_too_many_redirects_are_refused(self):
        responses = [
            _Response(status=302, headers={"location": f"https://example.com/hop{i}"})
            for i in range(extraction.MAX_REDIRECTS + 1)
        ]
        with self.assertRaises(ExtractionError) as caught:
            self.fetch(responses)
        self.assertIn("too many", str(caught.exception))

    def test_a_missing_redirect_destination_is_refused(self):
        with self.assertRaises(ExtractionError):
            self.fetch([_Response(status=302, headers={})])

    def test_a_non_200_answer_is_refused(self):
        with self.assertRaises(ExtractionError):
            self.fetch([_Response(status=404, headers={"content-type": "text/html"})])

    def test_a_file_that_is_not_a_web_page_is_refused(self):
        with self.assertRaises(ExtractionError):
            self.fetch([_Response(headers={"content-type": "application/pdf"}, body=b"%PDF-")])

    def test_an_oversized_page_is_refused(self):
        with patch.object(extraction, "MAX_DOWNLOAD_BYTES", 10):
            with self.assertRaises(ExtractionError) as caught:
                self.fetch(
                    [_Response(headers={"content-type": "text/plain"}, body=b"0123456789AB")]
                )
        self.assertIn("10 MB", str(caught.exception))

    def test_a_page_with_no_text_is_refused(self):
        with self.assertRaises(ExtractionError):
            self.fetch(
                [
                    _Response(
                        headers={"content-type": "text/html"},
                        body=b"<html><body><nav>x</nav></body></html>",
                    )
                ]
            )

    def test_a_timeout_is_plainly_refused(self):
        class TimeoutClient(_Client):
            def stream(self, method, url, headers=None):
                import httpx

                raise httpx.TimeoutException("slow")

        with patch.object(extraction.socket, "getaddrinfo", side_effect=public_dns), patch(
            "httpx.Client", return_value=TimeoutClient([])
        ):
            with self.assertRaises(ExtractionError) as caught:
                extraction.fetch_url("https://example.com/fees")
        self.assertIn("too long", str(caught.exception))


class ChunkingTests(SimpleTestCase):
    def test_a_short_document_is_one_passage(self):
        text = "Fees are due each term and must be paid on time."
        chunks = chunking.split_text(text)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, text)
        self.assertEqual(chunks[0].heading, "")

    def test_too_little_text_is_refused(self):
        with self.assertRaises(chunking.ChunkingError):
            chunking.split_text("Too short.")

    def test_heading_paths_are_kept(self):
        chunks = chunking.split_text(
            "# Fees\nFees are due at the start of each term and must be paid on time.\n"
            "## Payment plans\nFamilies may pay in three instalments across the term."
        )
        self.assertEqual([chunk.heading for chunk in chunks], ["Fees", "Fees › Payment plans"])

    def test_a_new_top_level_heading_resets_the_path(self):
        chunks = chunking.split_text(
            "# Fees\nFees are due at the start of each term and must be paid on time.\n"
            "## Plans\nFamilies may pay in three instalments across the term.\n"
            "# Transport\nBuses leave at six in the morning from the main gate."
        )
        self.assertEqual(
            [chunk.heading for chunk in chunks],
            ["Fees", "Fees › Plans", "Transport"],
        )

    def test_long_text_is_split_with_overlap(self):
        body = ". ".join(f"Sentence number {number} about school fees" for number in range(120))
        chunks = chunking.split_text(body)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.text), chunking.CHUNK_TARGET)
        last_words = chunks[0].text.split()[-4:]
        self.assertIn(" ".join(last_words), chunks[1].text)

    def test_no_passage_starts_mid_word(self):
        body = "word " * 500
        chunks = chunking.split_text(body)
        for chunk in chunks[1:]:
            self.assertTrue(chunk.text.startswith("word"))

    def test_a_very_long_document_is_refused(self):
        body = "\n".join(f"# Section {number}\n{'Text ' * 20}" for number in range(61))
        with self.assertRaises(chunking.ChunkingError) as caught:
            chunking.split_text(body)
        self.assertIn("too long", str(caught.exception))

    def test_tiny_sections_are_dropped_but_large_ones_kept(self):
        chunks = chunking.split_text(
            "# A\n" + "a" * 60 + "\n# B\nfew words\n# C\n" + "c" * 60
        )
        self.assertEqual([chunk.heading for chunk in chunks], ["A", "C"])

    def test_carriage_returns_and_extra_spaces_are_normalised(self):
        chunks = chunking.split_text(
            "Fees   are due at the start of each term.\r\n\r\n\r\nPay on time, please."
        )
        self.assertIn("Fees are due at the start of each term.", chunks[0].text)
        self.assertIn("Pay on time, please.", chunks[0].text)
        self.assertNotIn("\r", chunks[0].text)
