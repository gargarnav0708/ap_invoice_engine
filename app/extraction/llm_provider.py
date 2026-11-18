"""Modular Groq and deterministic invoice extraction providers."""
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


def _is_completion_truncation(error: Exception) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in (
        "max_completion_tokens",
        "max completion tokens",
        "output was truncated",
        "truncated to fit",
        "missing required content",
        "reached before generating a valid document",
        "finish_reason=length",
    ))


def _parse_amount_token(value: str) -> Optional[float]:
    """Parse common OCR punctuation variants without multiplying amounts by 1000."""
    token = re.sub(r"[^\d.,-]", "", value.strip())
    if not token or not re.search(r"\d", token):
        return None

    if "," in token and "." in token:
        decimal_separator = "." if token.rfind(".") > token.rfind(",") else ","
        grouping_separator = "," if decimal_separator == "." else "."
        token = token.replace(grouping_separator, "").replace(decimal_separator, ".")
    elif token.count(",") > 1:
        parts = token.split(",")
        if len(parts[-1]) == 2 and all(len(part) == 3 for part in parts[1:-1]):
            token = "".join(parts[:-1]) + "." + parts[-1]
        else:
            token = "".join(parts)
    elif "," in token:
        left, right = token.rsplit(",", 1)
        token = left + ("." + right if len(right) == 2 else right)
    elif "." in token:
        left, right = token.rsplit(".", 1)
        if len(right) == 3 and left.replace("-", "").isdigit():
            token = left + right
    try:
        return round(float(token), 2)
    except ValueError:
        return None


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


class GroqProvider(BaseLLMProvider):
    """Live Groq provider using its OpenAI-compatible chat completions API."""

    def __init__(self, api_key: str, base_url: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    @property
    def provider_name(self) -> str:
        return f"Groq ({self.model})"

    def extract_invoice(self, document_text: str) -> ExtractedInvoice:
        try:
            logger.info(f"Calling Groq ({self.model}) for invoice extraction...")
            messages = [
                {"role": "system", "content": SYSTEM_EXTRACTION_PROMPT},
                {"role": "user", "content": build_user_prompt(document_text)}
            ]
            completion = self.client.chat.completions
            initial_budget = max(256, min(int(settings.LLM_MAX_COMPLETION_TOKENS), 65536))
            configured_retry = int(settings.LLM_RETRY_MAX_COMPLETION_TOKENS)
            retry_budget = min(65536, max(configured_retry, initial_budget * 2))

            def request(response_format: dict[str, Any], budget: int) -> Any:
                parameters: dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "response_format": response_format,
                    "temperature": 0.0,
                    "max_completion_tokens": budget,
                    "timeout": 45.0,
                }
                # Groq documents reasoning_effort and include_reasoning for GPT-OSS.
                # Because the OpenAI Python SDK validates top-level keyword arguments on
                # Completions.create(), provider-specific body fields like include_reasoning
                # must be passed via extra_body.
                if self.model.startswith("openai/gpt-oss-"):
                    effort = settings.LLM_REASONING_EFFORT
                    parameters["reasoning_effort"] = effort if effort in {"low", "medium", "high"} else "low"
                    parameters["extra_body"] = {"include_reasoning": False}
                try:
                    return completion.create(**parameters)
                except Exception as error:
                    err_lower = str(error).lower()
                    if (
                        getattr(error, "status_code", None) == 400
                        and "include_reasoning" in err_lower
                        and "extra_body" in parameters
                    ):
                        logger.warning("Provider rejected include_reasoning; retrying without extra_body.")
                        parameters.pop("extra_body", None)
                        return completion.create(**parameters)
                    if (
                        _is_completion_truncation(error)
                        or getattr(error, "status_code", None) != 400
                        or "failed to validate json" not in err_lower
                        or response_format.get("type") != "json_schema"
                    ):
                        raise
                    logger.warning("Groq rejected schema-constrained output; retrying with JSON mode.")
                    parameters["response_format"] = {"type": "json_object"}
                    return completion.create(**parameters)

            try:
                response = request({
                    "type": "json_schema",
                    "json_schema": {
                        "name": "invoice_extraction",
                        "strict": True,
                        "schema": build_strict_extraction_schema(),
                    },
                }, initial_budget)
                first_choice = response.choices[0] if response.choices else None
                if getattr(first_choice, "finish_reason", None) == "length":
                    raise RuntimeError("Groq completion finish_reason=length; structured output was truncated.")
            except Exception as error:
                if not _is_completion_truncation(error) or retry_budget <= initial_budget:
                    raise
                logger.warning(
                    "Groq truncated invoice extraction at %s completion tokens; retrying once with %s.",
                    initial_budget,
                    retry_budget,
                )
                response = request({
                    "type": "json_schema",
                    "json_schema": {
                        "name": "invoice_extraction",
                        "strict": True,
                        "schema": build_strict_extraction_schema(),
                    },
                }, retry_budget)
                retry_choice = response.choices[0] if response.choices else None
                if getattr(retry_choice, "finish_reason", None) == "length":
                    raise ExtractionError(
                        f"Invoice extraction was still truncated after retrying with {retry_budget} completion tokens."
                    )

            raw_content = response.choices[0].message.content
            if not raw_content:
                raise ExtractionError("Live LLM returned an empty response.")
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

        def evidence_page(evidence: Optional[str]) -> Optional[int]:
            if not evidence:
                return None
            evidence_position = document_text.find(evidence)
            if evidence_position < 0:
                return None
            markers = list(
                re.finditer(
                    r"--- Page (\d+) ---",
                    document_text[: evidence_position + 1],
                )
            )
            return int(markers[-1].group(1)) if markers else None

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
        date_pattern = r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|[A-Za-z]{3,9}\s+\d{1,2},?\s*\d{4})"
        date_val, date_conf, date_ev = find_regex(rf"(?:Invoice\s*Date|Date):?\s*{date_pattern}", document_text)
        due_val, due_conf, due_ev = find_regex(rf"(?:Due\s*Date|Payment\s*Due):?\s*{date_pattern}", document_text)
        paid_val, paid_conf, paid_ev = find_regex(rf"(?:Paid|Payment\s*Date):?\s*\(?\s*{date_pattern}", document_text)

        # 5. PO Reference
        po_val, po_conf, po_ev = find_regex(r"(?:PO\s*(?:Reference|Ref|Number|#)?\s*:\s*)([A-Za-z0-9\-_]+)", document_text)
        if not po_val:
            po_val, po_conf, po_ev = find_regex(r"\b(PO-\d{4}-\d{3,4})\b", document_text, group=1)

        # 6. Currency
        curr_val, curr_conf, curr_ev = None, 0.0, None
        currency_code = re.search(r"\b(USD|EUR|GBP|CAD|AUD|NZD|HKD|SGD|AED|JPY|CNY|INR)\b", document_text, re.IGNORECASE)
        unique_symbols = (
            ("€", "EUR"), ("£", "GBP"), ("₹", "INR"), ("US$", "USD"),
            ("HK$", "HKD"), ("CA$", "CAD"), ("A$", "AUD"), ("NZ$", "NZD"), ("S$", "SGD"),
        )
        if currency_code:
            curr_val, curr_conf, curr_ev = currency_code.group(1).upper(), 0.95, currency_code.group(0)
        else:
            symbol_match = next(((symbol, code) for symbol, code in unique_symbols if symbol in document_text), None)
            rs_match = re.search(r"\b(Rs\.?|Rupees?)\b", document_text, re.IGNORECASE)
            if symbol_match:
                curr_ev, curr_val = symbol_match
                curr_conf = 0.90
            elif rs_match:
                curr_ev, curr_val, curr_conf = rs_match.group(0), "INR", 0.90
            elif "$" in document_text:
                # Bare dollar signs are preserved as evidence and resolved against PO/vendor context in the workflow.
                curr_ev = "$"

        # 7. Financial Totals
        curr_prefix = r"(?:[\$€£₹]|Rs\.?\s*|INR\s*|USD\s*|EUR\s*|GBP\s*)?"
        subtotal_val, subtotal_conf, subtotal_ev = None, 0.0, None
        sub_match = re.search(rf"Subtotal:?\s*[\n\r]*\s*{curr_prefix}\s*([\d][\d.,]*)", document_text, re.IGNORECASE)
        if sub_match:
            subtotal_val = _parse_amount_token(sub_match.group(1))
            if subtotal_val is not None:
                subtotal_conf, subtotal_ev = 0.95, sub_match.group(0)

        tax_val_amt, tax_amt_conf, tax_amt_ev = None, 0.0, None
        tax_match = re.search(rf"(?:Tax|GST|IGST|CGST|VAT)(?:\s*\(\d+%\))?:?\s*[\n\r]*\s*{curr_prefix}\s*([\d][\d.,]*)", document_text, re.IGNORECASE)
        if tax_match:
            tax_val_amt = _parse_amount_token(tax_match.group(1))
            if tax_val_amt is not None:
                tax_amt_conf, tax_amt_ev = 0.95, tax_match.group(0)

        total_val, total_conf, total_ev = None, 0.0, None
        # Prioritize matching "Total Amount:"
        total_match = re.search(rf"Total\s*Amount:?\s*[\n\r]*\s*{curr_prefix}\s*([\d][\d.,]*)", document_text, re.IGNORECASE)
        if not total_match:
            # Fall back to "Total:" but avoid single word column header "Total"
            total_match = re.search(rf"(?:Final\s*Total|Total\s*Due|Grand\s*Total|Total):?\s*(?:[\$€£₹]|Rs\.?|INR|USD|EUR|GBP)\s*([\d][\d.,]*)", document_text, re.IGNORECASE)
        if total_match:
            total_val = _parse_amount_token(total_match.group(1))
            if total_val is not None:
                total_conf, total_ev = 0.95, total_match.group(0)

        # 8. Line Items (from extracted table or pipe format)
        extracted_line_items = []
        for line in lines:
            # Table row format: ITEM: Industrial Safety Helmets | Qty: 20.0 | Price: $50.00 | Total: $1000.00
            table_match = re.search(
                rf"^ITEM:\s*([^|]+)\s*\|\s*Qty:\s*([\d.]+)\s*\|\s*Price:\s*{curr_prefix}([\d,.]+)\s*\|\s*Total:\s*{curr_prefix}([\d,.]+)",
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
                        description=ExtractedField(value=desc, confidence=0.95, evidence=line, page=evidence_page(line), extraction_method="synthetic"),
                        quantity=ExtractedField(value=qty, confidence=0.95, evidence=line, page=evidence_page(line), extraction_method="synthetic"),
                        unit=ExtractedField(value="EA", confidence=0.90, evidence=line, page=evidence_page(line), extraction_method="synthetic"),
                        unit_price=ExtractedField(value=price, confidence=0.95, evidence=line, page=evidence_page(line), extraction_method="synthetic"),
                        tax_amount=ExtractedField(value=0.0, confidence=0.80, evidence=line, page=evidence_page(line), extraction_method="synthetic"),
                        line_total=ExtractedField(value=ltotal, confidence=0.95, evidence=line, page=evidence_page(line), extraction_method="synthetic"),
                    )
                )

        invoice = ExtractedInvoice(
            vendor_name=ExtractedField(value=vendor_val, confidence=vendor_conf, evidence=vendor_ev, page=evidence_page(vendor_ev), extraction_method="synthetic"),
            vendor_address=ExtractedField(value=None, confidence=0.0, extraction_method="synthetic"),
            vendor_tax_id=ExtractedField(value=tax_val, confidence=tax_conf, evidence=tax_ev, page=evidence_page(tax_ev), extraction_method="synthetic"),
            invoice_number=ExtractedField(value=inv_val, confidence=inv_conf, evidence=inv_ev, page=evidence_page(inv_ev), extraction_method="synthetic"),
            invoice_date=ExtractedField(value=date_val, confidence=date_conf, evidence=date_ev, page=evidence_page(date_ev), extraction_method="synthetic"),
            due_date=ExtractedField(value=due_val, confidence=due_conf, evidence=due_ev, page=evidence_page(due_ev), extraction_method="synthetic"),
            paid_date=ExtractedField(value=paid_val, confidence=paid_conf, evidence=paid_ev, page=evidence_page(paid_ev), extraction_method="synthetic"),
            po_reference=ExtractedField(value=po_val, confidence=po_conf, evidence=po_ev, page=evidence_page(po_ev), extraction_method="synthetic"),
            currency=ExtractedField(value=curr_val, confidence=curr_conf, evidence=curr_ev, page=evidence_page(curr_ev), extraction_method="synthetic"),
            subtotal=ExtractedField(value=subtotal_val, confidence=subtotal_conf, evidence=subtotal_ev, page=evidence_page(subtotal_ev), extraction_method="synthetic"),
            tax_amount=ExtractedField(value=tax_val_amt, confidence=tax_amt_conf, evidence=tax_amt_ev, page=evidence_page(tax_amt_ev), extraction_method="synthetic"),
            total_amount=ExtractedField(value=total_val, confidence=total_conf, evidence=total_ev, page=evidence_page(total_ev), extraction_method="synthetic"),
            line_items=extracted_line_items,
            raw_text=document_text,
            is_mock=True
        )

        invoice.overall_confidence = compute_overall_confidence(invoice)
        return invoice


Tuple_Result = tuple[Optional[str], float, Optional[str]]


def get_llm_provider(mode: str | None = None) -> BaseLLMProvider:
    """Return the explicitly selected provider, with a safe offline fallback."""
    selected_mode = mode or settings.default_extraction_mode
    if selected_mode == "live" and settings.has_live_llm:
        logger.info(f"Using live Groq provider at {settings.LLM_BASE_URL}")
        return GroqProvider(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=settings.LLM_MODEL
        )
    if selected_mode == "live":
        logger.warning(
            "Live extraction requested without an LLM API key; using demo mode."
        )
    else:
        logger.info("Using explicitly selected Demo / Offline extractor.")
    return MockLLMProvider()
