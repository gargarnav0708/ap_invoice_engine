"""
Document classifier for identifying PDF nature (Machine-readable, Scanned, Hybrid, Corrupt).
"""
import pymupdf
from typing import Tuple, Dict, Any
from app.core.schemas import DocumentType
from app.utils.logging import get_logger

logger = get_logger("document_classifier")


def classify_document(doc: pymupdf.Document) -> Tuple[DocumentType, Dict[str, Any]]:
    """
    Examines text content and images in a PyMuPDF Document.
    Returns (DocumentType, metadata_dict).
    """
    total_pages = len(doc)
    if total_pages == 0:
        return DocumentType.INVALID_OR_CORRUPT, {"error": "Document contains 0 pages"}

    total_chars = 0
    total_images = 0
    pages_with_text = 0
    pages_with_images = 0

    for page_idx in range(total_pages):
        page = doc[page_idx]
        text = page.get_text("text").strip()
        char_count = len(text)
        total_chars += char_count

        images = page.get_images()
        img_count = len(images)
        total_images += img_count

        if char_count > 50:
            pages_with_text += 1
        if img_count > 0:
            pages_with_images += 1

    avg_chars_per_page = total_chars / total_pages

    meta = {
        "total_pages": total_pages,
        "total_chars": total_chars,
        "avg_chars_per_page": avg_chars_per_page,
        "total_images": total_images,
        "pages_with_text": pages_with_text,
        "pages_with_images": pages_with_images,
    }

    # Classification logic
    if avg_chars_per_page >= 80:
        if pages_with_images > 0 and pages_with_text < total_pages:
            doc_type = DocumentType.HYBRID
        else:
            doc_type = DocumentType.MACHINE_READABLE
    elif total_images > 0:
        doc_type = DocumentType.SCANNED_IMAGE
    elif total_chars > 0:
        doc_type = DocumentType.HYBRID
    else:
        doc_type = DocumentType.SCANNED_IMAGE

    logger.info(f"Classified PDF: {doc_type.value} (pages={total_pages}, chars={total_chars}, imgs={total_images})")
    return doc_type, meta
