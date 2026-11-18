"""
Business rules configuration and thresholds for AP Automation.
All thresholds, tolerances, and rule parameters live here.
"""
from dataclasses import dataclass, field
from pydantic import BaseModel, ConfigDict, Field, model_validator


@dataclass
class MatchingConfig:
    # Vendor matching
    VENDOR_EXACT_MATCH_SCORE: float = 1.0
    VENDOR_HIGH_CONFIDENCE_THRESHOLD: float = 0.88
    VENDOR_REVIEW_THRESHOLD: float = 0.60

    # PO matching
    PO_EXACT_MATCH_SCORE: float = 1.0
    PO_SIMILARITY_THRESHOLD: float = 0.75

    # Line item matching
    LINE_ITEM_DESCRIPTION_SIMILARITY_THRESHOLD: float = 0.65
    LINE_ITEM_PRICE_TOLERANCE_PCT: float = 0.01  # 1% price tolerance

    # Price/quantity variance tolerances (invoice line vs matched PO line).
    # Within tolerance -> approve with an informational note.
    # Outside tolerance -> flag for human review (soft failure).
    LINE_ITEM_PRICE_REVIEW_PCT: float = 0.05        # >5% unit-price variance -> review
    LINE_ITEM_QTY_OVERAGE_TOLERANCE_ABS: float = 0.001  # any qty billed over the PO line -> review


@dataclass
class FinancialValidationConfig:
    # Rounding and arithmetic tolerance
    ARITHMETIC_TOLERANCE_ABS: float = 0.05  # $0.05 absolute tolerance
    ARITHMETIC_TOLERANCE_PCT: float = 0.001  # 0.1% tolerance

    # PO balance tolerance
    PO_BALANCE_TOLERANCE_ABS: float = 0.05
    ALLOW_PARTIAL_OVERBILLING: bool = False

    # Tax rate reasonableness bounds (e.g., 0% to 35%)
    MAX_REASONABLE_TAX_RATE_PCT: float = 0.35


@dataclass
class DuplicateDetectionConfig:
    # File hash duplicate (Level 1)
    BLOCK_EXACT_FILE_HASH: bool = True

    # Vendor + Invoice Number exact match (Level 2)
    BLOCK_VENDOR_INVOICE_NUM_EXACT: bool = True

    # Near-duplicate threshold: same vendor, similar amount within X days
    NEAR_DUPLICATE_DAYS_WINDOW: int = 7
    NEAR_DUPLICATE_AMOUNT_TOLERANCE_ABS: float = 1.00


@dataclass
class ExtractionConfig:
    # Confidence threshold to flag for human review
    MIN_OVERALL_CONFIDENCE_THRESHOLD: float = 0.75
    MIN_CRITICAL_FIELD_CONFIDENCE_THRESHOLD: float = 0.70
    MIN_OCR_CONFIDENCE_THRESHOLD: float = 55.0

    # Critical fields required for automated processing
    CRITICAL_FIELDS: tuple = (
        "vendor_name",
        "invoice_number",
        "invoice_date",
        "total_amount",
        "currency",
    )


@dataclass
class DecisionPolicyConfig:
    # Hard failure rules (any failure results in BLOCKED)
    HARD_FAILURE_RULES: tuple = (
        "RULE_CONFIRMED_DUPLICATE",
        "RULE_VENDOR_UNAPPROVED",
        "RULE_PO_CLOSED_OR_CANCELLED",
        "RULE_PO_BALANCE_EXCEEDED",
        "RULE_CURRENCY_MISMATCH",
    )

    # Soft failure rules (triggers NEEDS_REVIEW)
    SOFT_FAILURE_RULES: tuple = (
        "RULE_POTENTIAL_DUPLICATE",
        "RULE_VENDOR_AMBIGUOUS",
        "RULE_PO_AMBIGUOUS",
        "RULE_LOW_EXTRACTION_CONFIDENCE",
        "RULE_MISSING_CRITICAL_FIELD",
        "RULE_LINE_ITEM_MISMATCH",
        "RULE_LINE_ITEM_PRICE_VARIANCE",
        "RULE_LINE_ITEM_QTY_OVERAGE",
        "RULE_TAX_CALCULATION_DISCREPANCY",
        "RULE_ARITHMETIC_DISCREPANCY",
    )


@dataclass
class AppRulesConfig:
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    financial: FinancialValidationConfig = field(default_factory=FinancialValidationConfig)
    duplicates: DuplicateDetectionConfig = field(default_factory=DuplicateDetectionConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    policy: DecisionPolicyConfig = field(default_factory=DecisionPolicyConfig)


class EditableRuleThresholds(BaseModel):
    """Validated subset of policy thresholds exposed in the Settings UI."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    arithmetic_tolerance_abs: float = Field(default=0.05, ge=0.0, le=1.0)
    near_duplicate_days_window: int = Field(default=7, ge=1, le=60)
    vendor_high_confidence_threshold: float = Field(default=0.88, ge=0.60, le=1.0)
    min_overall_confidence_threshold: float = Field(default=0.75, ge=0.50, le=1.0)

    @model_validator(mode="after")
    def validate_vendor_threshold(self) -> "EditableRuleThresholds":
        if self.vendor_high_confidence_threshold < MatchingConfig.VENDOR_REVIEW_THRESHOLD:
            raise ValueError(
                "Vendor high-confidence threshold cannot be below the review threshold."
            )
        return self

    @classmethod
    def from_rules(cls, rules: AppRulesConfig) -> "EditableRuleThresholds":
        return cls(
            arithmetic_tolerance_abs=rules.financial.ARITHMETIC_TOLERANCE_ABS,
            near_duplicate_days_window=rules.duplicates.NEAR_DUPLICATE_DAYS_WINDOW,
            vendor_high_confidence_threshold=rules.matching.VENDOR_HIGH_CONFIDENCE_THRESHOLD,
            min_overall_confidence_threshold=rules.extraction.MIN_OVERALL_CONFIDENCE_THRESHOLD,
        )

    def apply_to(self, rules: AppRulesConfig) -> None:
        rules.financial.ARITHMETIC_TOLERANCE_ABS = self.arithmetic_tolerance_abs
        rules.duplicates.NEAR_DUPLICATE_DAYS_WINDOW = self.near_duplicate_days_window
        rules.matching.VENDOR_HIGH_CONFIDENCE_THRESHOLD = self.vendor_high_confidence_threshold
        rules.extraction.MIN_OVERALL_CONFIDENCE_THRESHOLD = (
            self.min_overall_confidence_threshold
        )


# Global default configuration instance
rules_config = AppRulesConfig()
