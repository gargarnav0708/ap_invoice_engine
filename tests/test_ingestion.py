"""
Unit tests for document ingestion, classification, and OCR abstraction.
"""
from pathlib import Path
import pytest
import pymupdf
from PIL import Image

from app.core.schemas import DocumentType
from app.ingestion.document_classifier import classify_document
from app.ingestion.ocr_engine import FallbackOCREngine, get_ocr_engine
from app.ingestion.pdf_loader import PDFLoader
from app.config.settings import settings


def test_classify_machine_readable_document():
    doc = pymupdf.open(str(settings.SAMPLE_DATA_DIR / "scenario_1_clean_invoice.pdf"))
    doc_type, meta = classify_document(doc)
    assert doc_type == DocumentType.MACHINE_READABLE
    assert meta["total_chars"] > 100
    doc.close()


def test_classify_scanned_image_document(tmp_path):
    # Create a synthetic 1-page PDF containing only an image
    pdf_path = tmp_path / "scanned_doc.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)

    # Insert a blank 100x100 RGB image
    img = Image.new("RGB", (100, 100), color=(200, 200, 200))
    img_path = tmp_path / "temp_img.png"
    img.save(img_path)

    page.insert_image(pymupdf.Rect(50, 50, 200, 200), filename=str(img_path))
    doc.save(str(pdf_path))
    doc.close()

    doc2 = pymupdf.open(str(pdf_path))
    doc_type, meta = classify_document(doc2)
    assert doc_type == DocumentType.SCANNED_IMAGE
    assert meta["total_images"] >= 1
    assert meta["total_chars"] == 0
    doc2.close()


def test_ocr_engine_abstraction():
    engine = FallbackOCREngine()
    assert engine.is_available() is False
    dummy_img = Image.new("RGB", (50, 50))
    res = engine.extract_text_from_image(dummy_img)
    assert res == ""


def test_pdf_loader_ingestion():
    loader = PDFLoader()
    s1_file = settings.SAMPLE_DATA_DIR / "scenario_1_clean_invoice.pdf"
    res = loader.load_pdf(s1_file)
    assert res.total_pages == 1
    assert res.document_type == DocumentType.MACHINE_READABLE
    assert "Acme Industrial Supplies" in res.full_text
