"""
Invoice Extractor pipeline combining document ingestion, OCR, and LLM structured extraction.
"""
from typing import Optional
from pathlib import Path

from app.core.exceptions import ExtractionError
from app.core.schemas import ExtractedInvoice
from app.ingestion.pdf_loader import PDFLoader, IngestionResult
from app.extraction.llm_provider import get_llm_provider, BaseLLMProvider
from app.utils.logging import get_logger

logger = get_logger("extractor")


class InvoiceExtractor:
    def __init__(self, provider: Optional[BaseLLMProvider] = None):
        self.pdf_loader = PDFLoader()
        self.llm_provider = provider or get_llm_provider()

    def extract_from_pdf(self, file_path: Path) -> tuple[ExtractedInvoice, IngestionResult]:
        """
        Loads PDF, runs classification/OCR, and extracts structured invoice data.
        Returns (ExtractedInvoice, IngestionResult).
        """
        logger.info(f"Starting extraction pipeline for {file_path.name}")

        # Step 1: Ingestion and text extraction (with OCR fallback if scanned)
        ingestion_res = self.pdf_loader.load_pdf(file_path)

        if not ingestion_res.full_text.strip():
            logger.warning(f"No extractable text found in {file_path.name}")
            # Empty invoice representation
            empty_inv = ExtractedInvoice(
                raw_text="",
                document_type=ingestion_res.document_type,
                overall_confidence=0.0
            )
            return empty_inv, ingestion_res

        # Step 2: LLM Structured Extraction
        extracted_data = self.llm_provider.extract_invoice(ingestion_res.full_text)
        extracted_data.document_type = ingestion_res.document_type

        logger.info(
            f"Extraction completed. Confidence: {extracted_data.overall_confidence:.2f}, "
            f"Line items: {len(extracted_data.line_items)}, Mock={extracted_data.is_mock}"
        )

        return extracted_data, ingestion_res
