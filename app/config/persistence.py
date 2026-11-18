"""Validated, durable application preferences."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from app.config.rules import EditableRuleThresholds, rules_config
from app.config.settings import settings
from app.database.entities import AppConfig
from app.utils.logging import get_logger
from app.utils.time import now_utc

logger = get_logger("config_persistence")

RULE_THRESHOLDS_KEY = "rule_thresholds"
EXTRACTION_PREFERENCES_KEY = "extraction_preferences"
ExtractionMode = Literal["demo", "live"]


class ExtractionPreferences(BaseModel):
    """Runtime extraction provider selection persisted without credentials."""

    model_config = ConfigDict(extra="forbid")
    mode: ExtractionMode = "demo"


class ConfigurationService:
    def __init__(self, session: Session):
        self.session = session

    def load_rule_thresholds(self) -> EditableRuleThresholds:
        record = self.session.get(AppConfig, RULE_THRESHOLDS_KEY)
        if record is None:
            thresholds = EditableRuleThresholds()
        else:
            try:
                thresholds = EditableRuleThresholds.model_validate_json(record.value)
            except ValidationError:
                logger.error(
                    "Stored rule thresholds are invalid; using safe defaults.",
                    exc_info=True,
                )
                thresholds = EditableRuleThresholds()
        thresholds.apply_to(rules_config)
        return thresholds

    def save_rule_thresholds(
        self, values: EditableRuleThresholds | dict[str, object]
    ) -> EditableRuleThresholds:
        thresholds = EditableRuleThresholds.model_validate(values)
        self._upsert(RULE_THRESHOLDS_KEY, thresholds.model_dump_json())
        thresholds.apply_to(rules_config)
        return thresholds

    def load_extraction_preferences(self) -> ExtractionPreferences:
        record = self.session.get(AppConfig, EXTRACTION_PREFERENCES_KEY)
        if record is None:
            return ExtractionPreferences.model_validate(
                {"mode": settings.default_extraction_mode}
            )
        try:
            preferences = ExtractionPreferences.model_validate_json(record.value)
        except ValidationError:
            logger.error(
                "Stored extraction preferences are invalid; using the safe default.",
                exc_info=True,
            )
            return ExtractionPreferences.model_validate(
                {"mode": settings.default_extraction_mode}
            )
        if preferences.mode == "live" and not settings.has_live_llm:
            logger.warning(
                "Live extraction was selected but credentials are unavailable; "
                "falling back to demo mode."
            )
            return ExtractionPreferences(mode="demo")
        return preferences

    def save_extraction_preferences(
        self, mode: str
    ) -> ExtractionPreferences:
        preferences = ExtractionPreferences.model_validate({"mode": mode})
        if preferences.mode == "live" and not settings.has_live_llm:
            raise ValueError(
                "Live extraction requires a configured LLM_API_KEY. "
                "The credential must be set in the runtime environment, not in the UI."
            )
        self._upsert(EXTRACTION_PREFERENCES_KEY, preferences.model_dump_json())
        return preferences

    def _upsert(self, key: str, value: str) -> None:
        record = self.session.get(AppConfig, key)
        if record is None:
            self.session.add(AppConfig(key=key, value=value))
        else:
            record.value = value
            record.updated_at = now_utc()
        self.session.flush()
