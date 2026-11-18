"""
Application-wide custom exceptions.
Distinguishes between business exceptions and technical failures.
"""

class APException(Exception):
    """Base exception for all AP Automation errors."""
    def __init__(
        self, message: str, details: dict[str, object] | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class TechnicalFailure(APException):
    """Raised when an infrastructure or runtime component fails (e.g. OCR crash, DB disconnect)."""
    pass


class BusinessException(APException):
    """Raised when a business rule or validation condition fails as part of normal processing."""
    pass


class IngestionError(TechnicalFailure):
    """Raised when a document cannot be read, decrypted, or opened."""
    pass


class ExtractionError(APException):
    """Raised when data extraction fails unexpectedly."""
    pass


class ValidationError(BusinessException):
    """Raised when document data fails validation checks."""
    pass


class MatchingError(BusinessException):
    """Raised when vendor, PO, or line items cannot be matched unambiguously."""
    pass


class DecisionError(BusinessException):
    """Raised when the decision engine cannot determine an outcome."""
    pass


class WorkflowError(TechnicalFailure):
    """Raised when a workflow orchestration stage encounters an unexpected error."""
    pass
