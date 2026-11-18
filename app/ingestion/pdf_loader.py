"""
PDF document loader and text extractor using PyMuPDF and pdfplumber with OCR fallback.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError
import io
import pymupdf
import pdfplumber

from app.core.exceptions import IngestionError
from app.core.schemas import DocumentType
from app.ingestion.document_classifier import classify_document
from app.ingestion.ocr_engine import get_ocr_engine
from app.utils.file_storage import SUPPORTED_IMAGE_EXTENSIONS
from app.utils.logging import get_logger

logger = get_logger("pdf_loader")


@dataclass
class PageData:
    page_number: int
    text: str
    char_count: int
    has_images: bool
    table_text: str = ""
    image_preview: Optional[Image.Image] = None
    ocr_confidence: Optional[float] = None
    ocr_words: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class IngestionResult:
    file_path: Path
    total_pages: int
    document_type: DocumentType
    full_text: str
    pages: List[PageData]
    metadata: Dict[str, Any] = field(default_factory=dict)
    ocr_applied: bool = False
    ocr_unavailable: bool = False
    ocr_confidence: Optional[float] = None
    ocr_words: List[Dict[str, Any]] = field(default_factory=list)


class PDFLoader:
    def __init__(self):
        self.ocr_engine = get_ocr_engine()

    def _extract_ocr(self, image: Image.Image) -> tuple[str, Optional[float], List[Dict[str, Any]]]:
        """Support confidence-aware engines and legacy OCR adapters."""
        word_method = getattr(self.ocr_engine, "extract_text_with_word_confidence", None)
        if callable(word_method):
            result = word_method(image)
            if isinstance(result, tuple) and len(result) == 3:
                return result
        confidence_method = getattr(self.ocr_engine, "extract_text_with_confidence", None)
        if callable(confidence_method):
            result = confidence_method(image)
            if isinstance(result, tuple) and len(result) == 2:
                return result[0], result[1], []
        return self.ocr_engine.extract_text_from_image(image), None, []

    def load_pdf(self, file_path: Path, render_preview: bool = True) -> IngestionResult:
        """
        Loads PDF, classifies document structure, extracts text and tables, and applies OCR if necessary.
        """
        return self.load_document(file_path, render_preview=render_preview)

    def load_document(self, file_path: Path, render_preview: bool = True) -> IngestionResult:
        """Load a supported PDF or raster image invoice and extract its text."""
        if not file_path.exists():
            raise IngestionError(f"Invoice document does not exist at {file_path}")

        if file_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
            return self._load_image(file_path, render_preview=render_preview)

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
        ocr_unavailable = False
        ocr_confidences: List[float] = []
        all_ocr_words: List[Dict[str, Any]] = []
        pages_data: List[PageData] = []
        full_text_parts: List[str] = []

        # Extract structured tables page-by-page so evidence retains its source.
        tables_by_page: Dict[int, List[str]] = {}
        try:
            with pdfplumber.open(str(file_path)) as plumber_pdf:
                for page_number, plumber_page in enumerate(
                    plumber_pdf.pages, start=1
                ):
                    try:
                        for table in plumber_page.extract_tables() or []:
                            for row in table:
                                if not row or not any(row):
                                    continue
                                cells = [
                                    str(cell).strip().replace("\n", " ")
                                    for cell in row
                                    if cell is not None
                                ]
                                if len(cells) < 4 or not cells[0]:
                                    continue
                                first_cell = cells[0].strip().lower()
                                if first_cell in {
                                    "description",
                                    "item",
                                    "item description",
                                    "invoice",
                                }:
                                    continue
                                tables_by_page.setdefault(page_number, []).append(
                                    f"ITEM: {cells[0]} | "
                                    f"Qty: {cells[1]} | Price: {cells[2]} | "
                                    f"Total: {cells[3]}"
                                )
                    except Exception as exc:
                        logger.debug(
                            "Table extraction skipped for page %s: %s",
                            page_number,
                            exc,
                        )
        except Exception as e:
            logger.debug(f"pdfplumber table extraction skipped: {e}")

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            extracted_text = page.get_text("text").strip()
            images_on_page = len(page.get_images()) > 0
            needs_ocr = len(extracted_text) < 50 and (
                images_on_page
                or doc_type in (DocumentType.SCANNED_IMAGE, DocumentType.HYBRID)
            )

            # Render page image if preview is needed or OCR is required
            page_image: Optional[Image.Image] = None
            if render_preview or needs_ocr:
                pix = page.get_pixmap(dpi=300 if needs_ocr else 150)
                img_bytes = pix.tobytes("png")
                page_image = Image.open(io.BytesIO(img_bytes))

            # Run OCR fallback if machine-readable text is insufficient
            if needs_ocr:
                page_ocr_confidence = None
                page_ocr_words: List[Dict[str, Any]] = []
                if page_image:
                    ocr_text, page_ocr_confidence, page_ocr_words = self._extract_ocr(page_image)
                    if ocr_text:
                        extracted_text = (extracted_text + "\n" + ocr_text).strip()
                        ocr_applied = True
                        if page_ocr_confidence is not None:
                            ocr_confidences.append(page_ocr_confidence)
                        for word in page_ocr_words:
                            word_with_page = {**word, "page": page_idx + 1}
                            all_ocr_words.append(word_with_page)
                    else:
                        ocr_unavailable = True
                else:
                    ocr_unavailable = True

            table_text = "\n".join(tables_by_page.get(page_idx + 1, []))
            page_text = extracted_text
            if table_text:
                page_text = (
                    f"{page_text}\n--- Page {page_idx + 1} Structured Table ---\n"
                    f"{table_text}"
                ).strip()

            pages_data.append(
                PageData(
                    page_number=page_idx + 1,
                    text=page_text,
                    char_count=len(page_text),
                    has_images=images_on_page,
                    table_text=table_text,
                    image_preview=page_image,
                    ocr_confidence=page_ocr_confidence if needs_ocr and page_image else None,
                    ocr_words=page_ocr_words if needs_ocr and page_image else [],
                )
            )
            if page_text:
                full_text_parts.append(f"--- Page {page_idx + 1} ---\n{page_text}")

        doc.close()

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
            ocr_applied=ocr_applied,
            ocr_unavailable=ocr_unavailable,
            ocr_confidence=min(ocr_confidences) if ocr_confidences else None,
            ocr_words=all_ocr_words,
        )

    def _load_image(self, file_path: Path, render_preview: bool = True) -> IngestionResult:
        """OCR each frame of a raster invoice, retaining page previews and text."""
        try:
            with Image.open(file_path) as source:
                image_format = source.format or file_path.suffix.lstrip(".").upper()
                image_size = source.size
                frames = [frame.copy() for frame in ImageSequence.Iterator(source)]
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise IngestionError(f"Failed to open invoice image: {exc}") from exc

        if not frames:
            raise IngestionError("Invoice image contains no pages.")

        pages: List[PageData] = []
        text_parts: List[str] = []
        ocr_applied = False
        ocr_unavailable = False
        ocr_confidences: List[float] = []
        all_ocr_words: List[Dict[str, Any]] = []
        for page_number, frame in enumerate(frames, start=1):
            preview = ImageOps.exif_transpose(frame).convert("RGB")
            text, page_ocr_confidence, page_ocr_words = self._extract_ocr(preview)
            text = text.strip()
            if text:
                ocr_applied = True
                text_parts.append(f"--- Page {page_number} ---\n{text}")
                if page_ocr_confidence is not None:
                    ocr_confidences.append(page_ocr_confidence)
                all_ocr_words.extend({**word, "page": page_number} for word in page_ocr_words)
            else:
                ocr_unavailable = True
            pages.append(PageData(
                page_number=page_number,
                text=text,
                char_count=len(text),
                has_images=True,
                image_preview=preview if render_preview else None,
                ocr_confidence=page_ocr_confidence,
                ocr_words=page_ocr_words,
            ))

        full_text = "\n\n".join(text_parts)
        logger.info(
            f"Ingested image invoice '{file_path.name}': {len(pages)} frame(s), "
            f"{len(full_text)} characters, OCR={ocr_applied}"
        )
        return IngestionResult(
            file_path=file_path,
            total_pages=len(pages),
            document_type=DocumentType.SCANNED_IMAGE,
            full_text=full_text,
            pages=pages,
            metadata={"image_format": image_format, "image_size": image_size},
            ocr_applied=ocr_applied,
            ocr_unavailable=ocr_unavailable,
            ocr_confidence=min(ocr_confidences) if ocr_confidences else None,
            ocr_words=all_ocr_words,
        )
