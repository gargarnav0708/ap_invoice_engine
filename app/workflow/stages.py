"""
Canonical, ordered labels of the 19 pipeline stages.

Shared by the upload live view and the persisted Live Processing View so both
show the same stage names and numbering.
"""

PIPELINE_STAGES: list[str] = [
    "1. Receive invoice",
    "2. Validate uploaded file",
    "3. Calculate file hash",
    "4. Check for exact duplicate",
    "5. Extract document text",
    "6. Run OCR when necessary",
    "7. Extract structured invoice information",
    "8. Validate extracted schema",
    "9. Normalise extracted fields",
    "10. Match vendor",
    "11. Match purchase order",
    "12. Detect duplicate invoices",
    "13. Match line items",
    "14. Run financial validation",
    "15. Run PO balance validation",
    "16. Generate final decision",
    "17. Save all results",
    "18. Update invoice and PO records",
    "19. Generate the final audit report",
]
