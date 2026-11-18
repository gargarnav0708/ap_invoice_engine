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
from app.extraction.llm_provider import MockLLMProvider
from app.processing.normalizer import normalize_invoice
from app.extraction.confidence import compute_evidence_ocr_confidence


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


@pytest.mark.skipif(not get_ocr_engine().is_available(), reason="Tesseract is not installed")
def test_low_resolution_invoice_is_preprocessed_and_key_fields_survive_ocr():
    fixture = Path(__file__).parent / "fixtures" / "fuzzy_invoice_low_res.png"
    result = PDFLoader().load_document(fixture, render_preview=False)

    assert result.ocr_applied is True
    assert result.ocr_confidence is not None and result.ocr_confidence >= 55.0
    assert "Jun 22, 2021" in result.full_text
    assert "Jun 27" in result.full_text
    assert "1,564" in result.full_text

    extracted = MockLLMProvider().extract_invoice(result.full_text)
    normalized = normalize_invoice(extracted)
    assert normalized.invoice_date == "2021-06-22"
    assert normalized.due_date == "2021-06-27"
    assert normalized.paid_date == "2021-06-22"
    assert normalized.subtotal == 1564.0
    assert normalized.total_amount == 1564.0
    assert extracted.currency.value is None
    assert extracted.currency.evidence == "$"
    assert compute_evidence_ocr_confidence(extracted.invoice_date.evidence, result.ocr_words) >= 55.0
    assert compute_evidence_ocr_confidence(extracted.due_date.evidence, result.ocr_words) >= 55.0
    assert compute_evidence_ocr_confidence(extracted.total_amount.evidence, result.ocr_words) >= 55.0


def test_pdf_loader_processes_every_page_and_preserves_table_evidence():
    loader = PDFLoader()
    loader.ocr_engine = FallbackOCREngine()
    fixture = Path(__file__).parent / "fixtures" / "multi_page_invoice.pdf"

    result = loader.load_pdf(fixture, render_preview=False)

    assert result.total_pages == 3
    assert [page.page_number for page in result.pages] == [1, 2, 3]
    assert "INV-MULTI-001" in result.pages[0].text
    assert "ITEM: Industrial Safety Helmets" in result.pages[1].table_text
    assert "--- Page 2 ---" in result.full_text
    assert result.pages[2].text == ""
    assert result.ocr_unavailable is True
