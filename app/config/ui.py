"""
Presentation-layer configuration.

These values tune how the Streamlit UI pages and refreshes data. They do not
influence extraction, matching, validation, or decisions (those live in
``app/config/rules.py``), so they are deliberately kept separate.
"""
import os

# Invoice History / Invoice Details pagination
PAGE_SIZE_OPTIONS: tuple[int, ...] = (10, 25, 50)
DEFAULT_PAGE_SIZE: int = 10
DETAILS_PICKER_PAGE_SIZE: int = 25

# Live Processing View
MAX_RUN_OPTIONS: int = 25           # runs offered in the selector (bounded query)
LIVE_REFRESH_SECONDS: float = 2.0   # polling interval while a run is RUNNING
# A RUNNING run older than this is presented as "stalled" and polling stops.
# The database record is left untouched; this is display-only.
STALE_RUN_MINUTES: int = 15

# Document preview
ZOOM_LEVELS: tuple[int, ...] = (50, 75, 100, 125, 150, 200)  # percent of panel width
DEFAULT_ZOOM: int = 100
ROTATION_DEGREES: tuple[int, ...] = (0, 90, 180, 270)
PREVIEW_BASE_DPI: int = 110
PREVIEW_MAX_DPI: int = 240
PREVIEW_MAX_PIXELS: int = 12_000_000  # caps a single rendered page bitmap

# Audit trail rows shown in Invoice Details (most recent first)
MAX_AUDIT_ROWS: int = 200

# Developer-only "Run Tests" control (see app/ui/settings_view.py)
TEST_RUNNER_TIMEOUT_SECONDS: int = 180
TEST_RUNNER_MAX_OUTPUT_CHARS: int = 20_000
TEST_RUNNER_ENV_FLAG: str = os.getenv("ENABLE_UI_TEST_RUNNER", "true")
