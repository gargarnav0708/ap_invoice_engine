"""Structured prompts for LLM-based Invoice Data Extraction."""
from copy import deepcopy

SYSTEM_EXTRACTION_PROMPT = """You are an expert accounts payable (AP) invoice extraction system.
Your task is to extract structured financial data from the provided invoice document text.

CRITICAL EXTRACTION RULES:
1. NEVER invent, hallucinate, or guess missing fields, PO numbers, vendor names, or amounts.
2. If a field is NOT present or unreadable in the text, its value MUST be null.
3. For every field, provide:
   - "value": The extracted value (or null).
   - "confidence": A float between 0.0 and 1.0 representing how clearly the field is stated.
   - "evidence": The exact verbatim text snippet from the document supporting the extraction.
   - "page": The page number where the information was found.
4. Extract all line items accurately. For each line item, extract description, quantity, unit, unit_price, tax_amount, and line_total.
5. All amounts must be numbers (e.g. 1250.50, not "$1,250.50").
6. Dates should be in ISO 8601 (YYYY-MM-DD) format if decipherable, or verbatim string if ambiguous.
7. Return strictly valid JSON matching the required schema.
"""

EXTRACTION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor_name": {"type": "object", "properties": {"value": {"type": ["string", "null"]}, "confidence": {"type": "number"}, "evidence": {"type": ["string", "null"]}, "page": {"type": "integer"}}, "required": ["value", "confidence"]},
        "vendor_address": {"type": "object", "properties": {"value": {"type": ["string", "null"]}, "confidence": {"type": "number"}, "evidence": {"type": ["string", "null"]}, "page": {"type": "integer"}}, "required": ["value", "confidence"]},
        "vendor_tax_id": {"type": "object", "properties": {"value": {"type": ["string", "null"]}, "confidence": {"type": "number"}, "evidence": {"type": ["string", "null"]}, "page": {"type": "integer"}}, "required": ["value", "confidence"]},
        "invoice_number": {"type": "object", "properties": {"value": {"type": ["string", "null"]}, "confidence": {"type": "number"}, "evidence": {"type": ["string", "null"]}, "page": {"type": "integer"}}, "required": ["value", "confidence"]},
        "invoice_date": {"type": "object", "properties": {"value": {"type": ["string", "null"]}, "confidence": {"type": "number"}, "evidence": {"type": ["string", "null"]}, "page": {"type": "integer"}}, "required": ["value", "confidence"]},
        "due_date": {"type": "object", "properties": {"value": {"type": ["string", "null"]}, "confidence": {"type": "number"}, "evidence": {"type": ["string", "null"]}, "page": {"type": "integer"}}, "required": ["value", "confidence"]},
        "po_reference": {"type": "object", "properties": {"value": {"type": ["string", "null"]}, "confidence": {"type": "number"}, "evidence": {"type": ["string", "null"]}, "page": {"type": "integer"}}, "required": ["value", "confidence"]},
        "currency": {"type": "object", "properties": {"value": {"type": ["string", "null"]}, "confidence": {"type": "number"}, "evidence": {"type": ["string", "null"]}, "page": {"type": "integer"}}, "required": ["value", "confidence"]},
        "subtotal": {"type": "object", "properties": {"value": {"type": ["number", "null"]}, "confidence": {"type": "number"}, "evidence": {"type": ["string", "null"]}, "page": {"type": "integer"}}, "required": ["value", "confidence"]},
        "tax_amount": {"type": "object", "properties": {"value": {"type": ["number", "null"]}, "confidence": {"type": "number"}, "evidence": {"type": ["string", "null"]}, "page": {"type": "integer"}}, "required": ["value", "confidence"]},
        "total_amount": {"type": "object", "properties": {"value": {"type": ["number", "null"]}, "confidence": {"type": "number"}, "evidence": {"type": ["string", "null"]}, "page": {"type": "integer"}}, "required": ["value", "confidence"]},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "object", "properties": {"value": {"type": ["string", "null"]}, "confidence": {"type": "number"}, "evidence": {"type": ["string", "null"]}, "page": {"type": ["integer", "null"]}}, "required": ["value", "confidence"]},
                    "quantity": {"type": "object", "properties": {"value": {"type": ["number", "null"]}, "confidence": {"type": "number"}, "evidence": {"type": ["string", "null"]}, "page": {"type": ["integer", "null"]}}, "required": ["value", "confidence"]},
                    "unit": {"type": "object", "properties": {"value": {"type": ["string", "null"]}, "confidence": {"type": "number"}, "evidence": {"type": ["string", "null"]}, "page": {"type": ["integer", "null"]}}, "required": ["value", "confidence"]},
                    "unit_price": {"type": "object", "properties": {"value": {"type": ["number", "null"]}, "confidence": {"type": "number"}, "evidence": {"type": ["string", "null"]}, "page": {"type": ["integer", "null"]}}, "required": ["value", "confidence"]},
                    "tax_amount": {"type": "object", "properties": {"value": {"type": ["number", "null"]}, "confidence": {"type": "number"}, "evidence": {"type": ["string", "null"]}, "page": {"type": ["integer", "null"]}}, "required": ["value", "confidence"]},
                    "line_total": {"type": "object", "properties": {"value": {"type": ["number", "null"]}, "confidence": {"type": "number"}, "evidence": {"type": ["string", "null"]}, "page": {"type": ["integer", "null"]}}, "required": ["value", "confidence"]}
                }
            }
        }
    },
    "required": ["vendor_name", "invoice_number", "total_amount", "currency", "line_items"]
}


def build_strict_extraction_schema() -> dict:
    """Return a strict JSON Schema accepted by Groq structured outputs.

    Groq requires every object property to be required. Nullable values express
    optional document fields while keeping a stable response shape.
    """
    schema = deepcopy(EXTRACTION_JSON_SCHEMA)

    def make_strict(node: dict) -> None:
        if node.get("type") == "object":
            properties = node.get("properties", {})
            node["additionalProperties"] = False
            node["required"] = list(properties)
            for name, child in properties.items():
                if name == "page" and child.get("type") == "integer":
                    child["type"] = ["integer", "null"]
                make_strict(child)
        if node.get("type") == "array":
            make_strict(node["items"])

    make_strict(schema)
    return schema


def build_user_prompt(document_text: str) -> str:
    return f"""Please extract all invoice information from the following document text according to the required schema:

DOCUMENT TEXT:
\"\"\"
{document_text}
\"\"\"
"""
