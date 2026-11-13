"""
Unit tests for deterministic decision engine and explainability.
"""
import pytest
from app.core.schemas import RuleResult, RuleStatus, RuleSeverity, DecisionStatus
from app.decision.decision_service import DecisionService


def test_decision_approved():
    service = DecisionService()
    rules = [
        RuleResult(
            rule_id="RULE_VENDOR_APPROVED",
            rule_name="Vendor Approved",
            category="VENDOR",
            result=RuleStatus.PASS,
            severity=RuleSeverity.INFO,
            explanation="Vendor is active."
        ),
        RuleResult(
            rule_id="RULE_TOTAL_SUM_CHECK",
            rule_name="Total Math Check",
            category="FINANCIAL",
            result=RuleStatus.PASS,
            severity=RuleSeverity.INFO,
            explanation="Math is correct."
        )
    ]
    decision = service.determine_decision(rules)
    assert decision.status == DecisionStatus.APPROVED
    assert len(decision.checks_passed) == 2
    assert "Ready for posting" in decision.recommended_action


def test_decision_blocked_on_hard_failure():
    service = DecisionService()
    rules = [
        RuleResult(
            rule_id="RULE_CONFIRMED_DUPLICATE",
            rule_name="Duplicate Invoice",
            category="DUPLICATE",
            result=RuleStatus.FAIL,
            severity=RuleSeverity.HARD,
            explanation="Exact binary duplicate detected."
        )
    ]
    decision = service.determine_decision(rules)
    assert decision.status == DecisionStatus.BLOCKED
    assert len(decision.checks_failed) == 1
    assert "BLOCKED" in decision.summary


def test_decision_needs_review_on_soft_failure():
    service = DecisionService()
    rules = [
        RuleResult(
            rule_id="RULE_MISSING_CRITICAL_FIELD",
            rule_name="Critical Fields Present",
            category="EXTRACTION",
            result=RuleStatus.FAIL,
            severity=RuleSeverity.SOFT,
            explanation="Missing invoice date."
        )
    ]
    decision = service.determine_decision(rules)
    assert decision.status == DecisionStatus.NEEDS_REVIEW
    assert "HUMAN REVIEW" in decision.summary
