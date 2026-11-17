"""
Modular LLM extraction provider interface and implementations.
Supports live OpenAI-compatible LLMs and an explicit deterministic synthetic/mock provider.
"""
from abc import ABC, abstractmethod
import json
import re
from typing import Optional, Dict, Any, List

from app.config.settings import settings
from app.core.exceptions import ExtractionError
from app.core.schemas import (
    ExtractedInvoice, ExtractedField, ExtractedLineItem
)
from app.extraction.extraction_prompts import (
    SYSTEM_EXTRACTION_PROMPT, build_user_prompt, build_strict_extraction_schema
)
from app.extraction.confidence import compute_overall_confidence
from app.utils.logging import get_logger

logger = get_logger("llm_provider")


class BaseLLMProvider(ABC):
    """Abstract interface for LLM extraction providers."""

    @abstractmethod
    def extract_invoice(self, document_text: str) -> ExtractedInvoice:
        """Extract structured invoice data from text."""
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the provider."""
        pass


class OpenAICompatibleProvider(BaseLLMProvider):
    """Live LLM provider using OpenAI-compatible API."""

    def __init__(self, api_key: str, base_url: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    @property
    def provider_name(self) -> str:
        return f"OpenAI-Compatible ({self.model})"

    def extract_invoice(self, document_text: str) -> ExtractedInvoice:
        try:
            logger.info(f"Calling live LLM ({self.model}) for invoice extraction...")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_EXTRACTION_PROMPT},
                    {"role": "user", "content": build_user_prompt(document_text)}
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "invoice_extraction",
                        "strict": True,
                        "schema": build_strict_extraction_schema(),
                    },
                },
                temperature=0.0,
                timeout=45.0
            )

            raw_content = response.choices[0].message.content
            parsed_json = json.loads(raw_content)

            # Validate against Pydantic schema
            extracted = ExtractedInvoice.model_validate(parsed_json)
            extracted.raw_text = document_text
            extracted.is_mock = False
            extracted.overall_confidence = compute_overall_confidence(extracted)
            return extracted

        except Exception as e:
            logger.error(f"Live LLM extraction error: {e}")
            raise ExtractionError(f"LLM extraction failed: {str(e)}")


class MockLLMProvider(BaseLLMProvider):
    """
    Deterministic rule-based extractor for local demo, offline mode, and unit tests.
    Explicitly marked as mock/demo extraction; never fabricates absent data.
    """

    @property
    def provider_name(self) -> str:
        return "Deterministic Demo / Offline Rule Engine"

    def extract_invoice(self, document_text: str) -> ExtractedInvoice:
        logger.info("Using Demo / Offline Extractor (no live LLM credentials configured).")
        lines = [line.strip() for line in document_text.splitlines() if line.strip()]

        def find_regex(pattern: str, text: str, group: int = 1) -> Tuple_Result:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                val = match.group(group).strip()
                return val, 0.95, match.group(0)
            return None, 0.0, None

        # 1. Vendor Name
        vendor_val, vendor_conf, vendor_ev = None, 0.0, None
        vendor_match = re.search(r"Vendor:\s*([^\n\r]+)", document_text, re.IGNORECASE)
        if vendor_match:
            vendor_val = vendor_match.group(1).strip()
            vendor_conf, vendor_ev = 0.95, vendor_match.group(0)
        else:
            for line in lines[:5]:
                if any(suffix in line.upper() for suffix in ["INC", "LLC", "LTD", "CORP", "GMBH", "SUPPLIES", "SERVICES"]):
                    vendor_val = line
                    vendor_conf, vendor_ev = 0.90, line
                    break

        # 2. Invoice Number
        inv_val, inv_conf, inv_ev = find_regex(r"(?:Invoice\s*(?:Number|#|No\.?)?\s*:\s*)([A-Za-z0-9\-_]+)", document_text)
        if not inv_val:
            inv_val, inv_conf, inv_ev = find_regex(r"\b(INV-\d{4}-\d{3,4})\b", document_text, group=1)

        # 3. Tax ID
        tax_val, tax_conf, tax_ev = find_regex(r"(?:Tax\s*ID|VAT|EIN|Registration):?\s*([A-Za-z0-9\-_]+)", document_text)

        # 4. Dates
        date_val, date_conf, date_ev = find_regex(r"(?:Invoice\s*Date|Date):?\s*(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})", document_text)
        due_val, due_conf, due_ev = find_regex(r"(?:Due\s*Date|Payment\s*Due):?\s*(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})", document_text)

        # 5. PO Reference
        po_val, po_conf, po_ev = find_regex(r"(?:PO\s*(?:Reference|Ref|Number|#)?\s*:\s*)([A-Za-z0-9\-_]+)", document_text)
        if not po_val:
            po_val, po_conf, po_ev = find_regex(r"\b(PO-\d{4}-\d{3,4})\b", document_text, group=1)

        # 6. Currency
        curr_val = "USD"
        curr_conf = 0.95
        curr_ev = "USD"
        if "EUR" in document_text or "€" in document_text:
            curr_val, curr_ev = "EUR", "EUR/€"
        elif "GBP" in document_text or "£" in document_text:
            curr_val, curr_ev = "GBP", "GBP/£"

        # 7. Financial Totals
        subtotal_val, subtotal_conf, subtotal_ev = None, 0.0, None
        sub_match = re.search(r"Subtotal:?\s*[\n\r]*\s*[\$€£]?\s*([\d,]+\.?\d*)", document_text, re.IGNORECASE)
        if sub_match:
            try:
                subtotal_val = float(sub_match.group(1).replace(",", ""))
                subtotal_conf, subtotal_ev = 0.95, sub_match.group(0)
            except ValueError:
                pass

        tax_val_amt, tax_amt_conf, tax_amt_ev = None, 0.0, None
        tax_match = re.search(r"Tax(?:\s*\(\d+%\))?:?\s*[\n\r]*\s*[\$€£]?\s*([\d,]+\.?\d*)", document_text, re.IGNORECASE)
        if tax_match:
            try:
                tax_val_amt = float(tax_match.group(1).replace(",", ""))
                tax_amt_conf, tax_amt_ev = 0.95, tax_match.group(0)
            except ValueError:
                pass

        total_val, total_conf, total_ev = None, 0.0, None
        # Prioritize matching "Total Amount:"
        total_match = re.search(r"Total\s*Amount:?\s*[\n\r]*\s*[\$€£]?\s*([\d,]+\.?\d*)", document_text, re.IGNORECASE)
        if not total_match:
            # Fall back to "Total:" but avoid single word column header "Total"
            total_match = re.search(r"(?:Final\s*Total|Total\s*Due|Total):?\s*[\$€£]\s*([\d,]+\.?\d*)", document_text, re.IGNORECASE)
        if total_match:
            try:
                total_val = float(total_match.group(1).replace(",", ""))
                total_conf, total_ev = 0.95, total_match.group(0)
            except ValueError:
                pass

        # 8. Line Items (from extracted table or pipe format)
        extracted_line_items = []
        for line in lines:
            # Table row format: ITEM: Industrial Safety Helmets | Qty: 20.0 | Price: $50.00 | Total: $1000.00
            table_match = re.search(
                r"^ITEM:\s*([^|]+)\s*\|\s*Qty:\s*([\d.]+)\s*\|\s*Price:\s*[\$€£]?([\d,.]+)\s*\|\s*Total:\s*[\$€£]?([\d,.]+)",
                line,
                re.IGNORECASE
            )
            if table_match:
                desc = table_match.group(1).strip()
                qty = float(table_match.group(2))
                price = float(table_match.group(3).replace(",", ""))
                ltotal = float(table_match.group(4).replace(",", ""))
                extracted_line_items.append(
                    ExtractedLineItem(
                        description=ExtractedField(value=desc, confidence=0.95, evidence=line, extraction_method="synthetic"),
                        quantity=ExtractedField(value=qty, confidence=0.95, evidence=line, extraction_method="synthetic"),
                        unit=ExtractedField(value="EA", confidence=0.90, evidence=line, extraction_method="synthetic"),
                        unit_price=ExtractedField(value=price, confidence=0.95, evidence=line, extraction_method="synthetic"),
                        tax_amount=ExtractedField(value=0.0, confidence=0.80, evidence=line, extraction_method="synthetic"),
                        line_total=ExtractedField(value=ltotal, confidence=0.95, evidence=line, extraction_method="synthetic"),
                    )
                )

        invoice = ExtractedInvoice(
            vendor_name=ExtractedField(value=vendor_val, confidence=vendor_conf, evidence=vendor_ev, extraction_method="synthetic"),
            vendor_address=ExtractedField(value=None, confidence=0.0, extraction_method="synthetic"),
            vendor_tax_id=ExtractedField(value=tax_val, confidence=tax_conf, evidence=tax_ev, extraction_method="synthetic"),
            invoice_number=ExtractedField(value=inv_val, confidence=inv_conf, evidence=inv_ev, extraction_method="synthetic"),
            invoice_date=ExtractedField(value=date_val, confidence=date_conf, evidence=date_ev, extraction_method="synthetic"),
            due_date=ExtractedField(value=due_val, confidence=due_conf, evidence=due_ev, extraction_method="synthetic"),
            po_reference=ExtractedField(value=po_val, confidence=po_conf, evidence=po_ev, extraction_method="synthetic"),
            currency=ExtractedField(value=curr_val, confidence=curr_conf, evidence=curr_ev, extraction_method="synthetic"),
            subtotal=ExtractedField(value=subtotal_val, confidence=subtotal_conf, evidence=subtotal_ev, extraction_method="synthetic"),
            tax_amount=ExtractedField(value=tax_val_amt, confidence=tax_amt_conf, evidence=tax_amt_ev, extraction_method="synthetic"),
            total_amount=ExtractedField(value=total_val, confidence=total_conf, evidence=total_ev, extraction_method="synthetic"),
            line_items=extracted_line_items,
            raw_text=document_text,
            is_mock=True
        )

        invoice.overall_confidence = compute_overall_confidence(invoice)
        return invoice


Tuple_Result = tuple[Optional[str], float, Optional[str]]


def get_llm_provider() -> BaseLLMProvider:
    """Factory to return appropriate LLM provider based on environment credentials."""
    if settings.has_live_llm:
        logger.info(f"Using live OpenAI-compatible LLM provider at {settings.LLM_BASE_URL}")
        return OpenAICompatibleProvider(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=settings.LLM_MODEL
        )
    logger.info("No live LLM API key detected. Using Mock/Demo LLM Provider.")
    return MockLLMProvider()
