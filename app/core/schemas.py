"""
Pydantic schemas for data validation, structured extraction, matching, and decisions.
"""
from enum import Enum
from typing import Optional, List, Dict, Any, Generic, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")


class RuleSeverity(str, Enum):
    HARD = "HARD"
    SOFT = "SOFT"
    INFO = "INFO"


class RuleStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"


class DecisionStatus(str, Enum):
    APPROVED = "APPROVED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED = "BLOCKED"


class StageStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class DocumentType(str, Enum):
    MACHINE_READABLE = "MACHINE_READABLE"
    SCANNED_IMAGE = "SCANNED_IMAGE"
    HYBRID = "HYBRID"
    INVALID_OR_CORRUPT = "INVALID_OR_CORRUPT"


# ==============================================================================
# Structured Extraction Schemas
# ==============================================================================

class ExtractedField(BaseModel, Generic[T]):
    """Represents an extracted invoice field with provenance and confidence."""
    value: Optional[T] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: Optional[str] = None
    page: Optional[int] = 1
    extraction_method: str = "llm"  # 'digital_text', 'ocr', 'llm', 'synthetic'


class ExtractedLineItem(BaseModel):
    """Structured line item extracted from an invoice."""
    description: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str]())
    quantity: ExtractedField[float] = Field(default_factory=lambda: ExtractedField[float]())
    unit: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str]())
    unit_price: ExtractedField[float] = Field(default_factory=lambda: ExtractedField[float]())
    tax_amount: ExtractedField[float] = Field(default_factory=lambda: ExtractedField[float]())
    line_total: ExtractedField[float] = Field(default_factory=lambda: ExtractedField[float]())


class ExtractedInvoice(BaseModel):
    """Complete structured invoice extracted from PDF."""
    vendor_name: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str]())
    vendor_address: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str]())
    vendor_tax_id: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str]())
    invoice_number: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str]())
    invoice_date: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str]())
    due_date: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str]())
    paid_date: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str]())
    po_reference: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str]())
    currency: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str]())
    subtotal: ExtractedField[float] = Field(default_factory=lambda: ExtractedField[float]())
    tax_amount: ExtractedField[float] = Field(default_factory=lambda: ExtractedField[float]())
    total_amount: ExtractedField[float] = Field(default_factory=lambda: ExtractedField[float]())
    line_items: List[ExtractedLineItem] = Field(default_factory=list)

    # Document-level metadata
    raw_text: Optional[str] = None
    document_type: DocumentType = DocumentType.MACHINE_READABLE
    is_mock: bool = False
    ocr_confidence: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    ocr_words: List[Dict[str, Any]] = Field(default_factory=list)
    ocr_field_confidences: Dict[str, float] = Field(default_factory=dict)
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)



# ==============================================================================
# Normalization Schemas
# ==============================================================================

class NormalizedLineItem(BaseModel):
    description: str
    quantity: float
    unit: Optional[str] = "EA"
    unit_price: float
    tax_amount: float = 0.0
    line_total: float


class NormalizedInvoice(BaseModel):
    vendor_name: Optional[str] = None
    normalized_vendor_name: Optional[str] = None
    vendor_tax_id: Optional[str] = None
    invoice_number: Optional[str] = None
    normalized_invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    paid_date: Optional[str] = None
    po_reference: Optional[str] = None
    normalized_po_reference: Optional[str] = None
    currency: Optional[str] = None
    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None
    total_amount: Optional[float] = None
    line_items: List[NormalizedLineItem] = Field(default_factory=list)


# ==============================================================================
# Matching Schemas
# ==============================================================================

class VendorMatchCandidate(BaseModel):
    vendor_id: int
    legal_name: str
    match_type: str  # 'exact_tax_id', 'exact_name', 'fuzzy_name'
    score: float
    explanation: str
    is_approved: bool


class POMatchCandidate(BaseModel):
    po_id: int
    po_number: str
    vendor_id: int
    score: float
    explanation: str
    total_amount: float
    remaining_balance: float
    currency: str
    status: str


class LineItemMatch(BaseModel):
    invoice_line_idx: int
    po_line_id: Optional[int] = None
    po_line_number: Optional[int] = None
    score: float = 0.0
    matched_description: Optional[str] = None
    price_diff: Optional[float] = None
    qty_diff: Optional[float] = None
    explanation: str = ""


# ==============================================================================
# Validation & Decision Schemas
# ==============================================================================

class RuleResult(BaseModel):
    rule_id: str
    rule_name: str
    category: str  # 'VENDOR', 'PO', 'DUPLICATE', 'FINANCIAL', 'LINE_ITEM', 'BALANCE', 'EXTRACTION'
    result: RuleStatus
    severity: RuleSeverity
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None
    difference: Optional[str] = None
    explanation: str
    evidence: Optional[str] = None


class DecisionResult(BaseModel):
    status: DecisionStatus
    summary: str
    checks_passed: List[str] = Field(default_factory=list)
    checks_failed: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
    evidence_items: List[Dict[str, Any]] = Field(default_factory=list)
    recommended_action: str


# ==============================================================================
# Workflow Schemas
# ==============================================================================

class StageExecutionRecord(BaseModel):
    stage_name: str
    status: StageStatus
    start_time: str
    completion_time: Optional[str] = None
    duration_ms: Optional[float] = None
    details: Optional[str] = None
    error: Optional[str] = None
