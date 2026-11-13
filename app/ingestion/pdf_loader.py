"""
PDF document loader and text extractor using PyMuPDF and pdfplumber with OCR fallback.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional
from PIL import Image
import io
import pymupdf
import pdfplumber

from app.core.exceptions import IngestionError
from app.core.schemas import DocumentType
from app.ingestion.document_classifier import classify_document
from app.ingestion.ocr_engine import get_ocr_engine
from app.utils.logging import get_logger

logger = get_logger("pdf_loader")


@dataclass
class PageData:
    page_number: int
    text: str
    char_count: int
    has_images: bool
    image_preview: Optional[Image.Image] = None


@dataclass
class IngestionResult:
    file_path: Path
    total_pages: int
    document_type: DocumentType
    full_text: str
    pages: List[PageData]
    metadata: Dict[str, Any] = field(default_factory=dict)
    ocr_applied: bool = False


class PDFLoader:
    def __init__(self):
        self.ocr_engine = get_ocr_engine()

    def load_pdf(self, file_path: Path, render_preview: bool = True) -> IngestionResult:
        """
        Loads PDF, classifies document structure, extracts text and tables, and applies OCR if necessary.
        """
        if not file_path.exists():
            raise IngestionError(f"PDF file does not exist at {file_path}")

        try:
            doc = pymupdf.open(str(file_path))
        except Exception as e:
            raise IngestionError(f"Failed to open PDF document: {e}")

        if doc.is_encrypted:
            raise IngestionError("PDF document is encrypted / password protected.")

        if len(doc) == 0:
            raise IngestionError("PDF document contains no pages.")

        # Classify document
        doc_type, meta = classify_document(doc)

        ocr_applied = False
        pages_data: List[PageData] = []
        full_text_parts: List[str] = []

        # Extract structured tables using pdfplumber where available
        extracted_tables_text = []
        try:
            with pdfplumber.open(str(file_path)) as plumber_pdf:
                for p_idx, p in enumerate(plumber_pdf.pages):
                    page_tables = p.extract_tables()
                    for tbl in page_tables:
                        for row in tbl:
                            if not row or not any(row):
                                continue
                            cleaned_cells = [str(cell).strip().replace("\n", " ") for cell in row if cell is not None]
                            if len(cleaned_cells) >= 4 and not any(h in cleaned_cells[0].lower() for h in ["description", "item", "invoice"]):
                                # Format as structured line item representation
                                desc = cleaned_cells[0]
                                qty = cleaned_cells[1]
                                price = cleaned_cells[2]
                                ltotal = cleaned_cells[3]
                                extracted_tables_text.append(f"ITEM: {desc} | Qty: {qty} | Price: {price} | Total: {ltotal}")
        except Exception as e:
            logger.debug(f"pdfplumber table extraction skipped: {e}")

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            extracted_text = page.get_text("text").strip()
            images_on_page = len(page.get_images()) > 0

            # Render page image if preview is needed or OCR is required
            page_image: Optional[Image.Image] = None
            if render_preview or (doc_type == DocumentType.SCANNED_IMAGE and len(extracted_text) < 50):
                pix = page.get_pixmap(dpi=150)
                img_bytes = pix.tobytes("png")
                page_image = Image.open(io.BytesIO(img_bytes))

            # Run OCR fallback if machine-readable text is insufficient
            if (doc_type in (DocumentType.SCANNED_IMAGE, DocumentType.HYBRID)) and len(extracted_text) < 50:
                if page_image:
                    ocr_text = self.ocr_engine.extract_text_from_image(page_image)
                    if ocr_text:
                        extracted_text = (extracted_text + "\n" + ocr_text).strip()
                        ocr_applied = True

            pages_data.append(
                PageData(
                    page_number=page_idx + 1,
                    text=extracted_text,
                    char_count=len(extracted_text),
                    has_images=images_on_page,
                    image_preview=page_image
                )
            )
            if extracted_text:
                full_text_parts.append(f"--- Page {page_idx + 1} ---\n{extracted_text}")

        doc.close()

        if extracted_tables_text:
            full_text_parts.append("--- Extracted Line Items Table ---\n" + "\n".join(extracted_tables_text))

        full_text = "\n\n".join(full_text_parts)
        logger.info(
            f"Successfully ingested '{file_path.name}': {len(pages_data)} pages, "
            f"{len(full_text)} characters, OCR={ocr_applied}"
        )

        return IngestionResult(
            file_path=file_path,
            total_pages=len(pages_data),
            document_type=doc_type,
            full_text=full_text,
            pages=pages_data,
            metadata=meta,
            ocr_applied=ocr_applied
        )
