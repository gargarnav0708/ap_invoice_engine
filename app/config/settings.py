import os
from pathlib import Path
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Load .env if present
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    # Project paths
    PROJECT_ROOT: Path = PROJECT_ROOT
    DATA_DIR: Path = PROJECT_ROOT / "data"
    STORAGE_DIR: Path = PROJECT_ROOT / "data" / "uploads"
    SAMPLE_DATA_DIR: Path = PROJECT_ROOT / "data" / "sample_data"

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{PROJECT_ROOT / 'data' / 'ap_invoices.db'}"
    )

    # LLM Provider
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str = os.getenv(
        "LLM_BASE_URL", "https://api.groq.com/openai/v1"
    )
    LLM_MODEL: str = os.getenv("LLM_MODEL", "openai/gpt-oss-20b")
    LLM_MAX_COMPLETION_TOKENS: int = int(os.getenv("LLM_MAX_COMPLETION_TOKENS", "8192"))
    LLM_RETRY_MAX_COMPLETION_TOKENS: int = int(os.getenv("LLM_RETRY_MAX_COMPLETION_TOKENS", "16384"))
    LLM_REASONING_EFFORT: str = os.getenv("LLM_REASONING_EFFORT", "low").strip().lower()
    ENABLE_DEMO_MODE: bool = os.getenv("ENABLE_DEMO_MODE", "true").lower() in ("true", "1", "yes")
    EXTRACTION_MODE: str = os.getenv("EXTRACTION_MODE", "")

    # OCR Settings
    OCR_ENABLED: bool = os.getenv("OCR_ENABLED", "true").lower() in ("true", "1", "yes")
    TESSERACT_CMD: str = os.getenv("TESSERACT_CMD", "")

    # General
    APP_ENV: str = os.getenv("APP_ENV", "development")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def has_live_llm(self) -> bool:
        """Returns True if LLM credentials are configured."""
        return bool(self.LLM_API_KEY.strip())

    @property
    def default_extraction_mode(self) -> str:
        """Return the safe startup mode, retaining ENABLE_DEMO_MODE compatibility."""
        configured = self.EXTRACTION_MODE.strip().lower()
        if configured == "live":
            return "live" if self.has_live_llm else "demo"
        if configured == "demo":
            return "demo"
        if self.ENABLE_DEMO_MODE:
            return "demo"
        return "live" if self.has_live_llm else "demo"

    def ensure_directories(self) -> None:
        """Create necessary project data directories."""
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        self.SAMPLE_DATA_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_directories()
