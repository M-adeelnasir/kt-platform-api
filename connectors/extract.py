"""Binary document text extraction for the Drive connector.

Pulls plain text out of PDF and Word (.docx) files. Scanned/image-only PDFs yield little or no
text (no OCR in the MVP) — callers should skip empty results.
"""

from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)


def extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception as exc:  # one bad page shouldn't kill the doc
            logger.warning("pdf page extract failed: %s", exc)
    return "\n".join(parts).strip()


def extract_docx(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs).strip()
