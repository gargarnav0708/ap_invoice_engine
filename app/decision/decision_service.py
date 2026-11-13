"""
Decision Service: Evaluates validation rule results and produces deterministic explainable decisions.
Never allows LLM override.
"""
from typing import List, Dict, Any
from app.core.schemas import (
    RuleResult, RuleStatus, RuleSeverity, DecisionResult, DecisionStatus
)
from app.config.rules import rules_config
from app.utils.logging import get_logger

logger = get_logger("decision_service")


class DecisionService:
    def determine_decision(self, validation_results: List[RuleResult]) -> DecisionResult:
        """
        Determines the final decision (APPROVED, NEEDS_REVIEW, BLOCKED) based on rule results.
        """
        checks_passed: List[str] = []
        checks_failed: List[str] = []
        warnings: List[str] = []
        reasons: List[str] = []
        evidence_items: List[Dict[str, Any]] = []

        has_hard_failure = False
        has_soft_failure = False
        has_warnings = False

        for rule in validation_results:
            rule_entry = {
                "rule_id": rule.rule_id,
                "rule_name": rule.rule_name,
                "category": rule.category,
                "result": rule.result.value,
                "severity": rule.severity.value,
                "expected": rule.expected_value,
                "actual": rule.actual_value,
                "explanation": rule.explanation,
                "evidence": rule.evidence
            }
            evidence_items.append(rule_entry)

            if rule.result == RuleStatus.PASS:
                checks_passed.append(f"[{rule.category}] {rule.rule_name}: {rule.explanation}")

            elif rule.result == RuleStatus.FAIL:
                if rule.severity == RuleSeverity.HARD:
                    has_hard_failure = True
                    checks_failed.append(f"[HARD FAILURE] {rule.rule_name}: {rule.explanation}")
                    reasons.append(f"Blocked due to hard failure: {rule.explanation}")
                else:
                    has_soft_failure = True
                    checks_failed.append(f"[SOFT FAILURE] {rule.rule_name}: {rule.explanation}")
                    reasons.append(f"Requires review: {rule.explanation}")

            elif rule.result == RuleStatus.WARN:
                has_warnings = True
                warnings.append(f"[WARNING] {rule.rule_name}: {rule.explanation}")
                reasons.append(f"Flagged for human verification: {rule.explanation}")

        # Final Status determination
        if has_hard_failure:
            status = DecisionStatus.BLOCKED
            summary = (
                f"Invoice BLOCKED. Encounted {len(checks_failed)} failure(s) violating mandatory AP policy."
            )
            recommended_action = (
                "Do not process payment. Investigate duplicate submission, unapproved vendor, or PO balance overbilling."
            )

        elif has_soft_failure or has_warnings:
            status = DecisionStatus.NEEDS_REVIEW
            summary = (
                f"Invoice requires HUMAN REVIEW. {len(warnings) + len(checks_failed)} exception(s) or ambiguities flagged."
            )
            recommended_action = (
                "An AP analyst must inspect the invoice evidence, verify the vendor or PO link, and confirm or correct fields before approval."
            )

        else:
            status = DecisionStatus.APPROVED
            summary = (
                f"Invoice AUTOMATICALLY APPROVED. All {len(checks_passed)} verification rules passed successfully."
            )
            recommended_action = (
                "Ready for posting to procurement ERP and scheduling for payment according to invoice terms."
            )

        logger.info(f"Decision evaluated: {status.value} (Hard={has_hard_failure}, Soft={has_soft_failure}, Warn={has_warnings})")

        return DecisionResult(
            status=status,
            summary=summary,
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            warnings=warnings,
            reasons=reasons,
            evidence_items=evidence_items,
            recommended_action=recommended_action
        )
