"""
In-app light/dark theme.

* The preference lives only in ``st.session_state`` (per browser session).
  There is no per-user account model, so it is never written to the database
  or to ``.streamlit/config.toml``.
* The startup default comes from the existing ``.streamlit/config.toml``
  theme (read-only).
* Styling uses our own ``ap-*`` classes for app components, plus a small set
  of Streamlit's public ``data-testid`` / ARIA hooks for native widgets in dark
  mode. Streamlit's generated (emotion) class names are never targeted.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

THEME_STATE_KEY = "ui_theme"
THEME_LABELS = {"light": "☀️ Light", "dark": "🌙 Dark"}

TOKENS: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#FFFFFF", "surface": "#F7FAFC", "surface-2": "#EDF2F7", "border": "#CBD5E0",
        "text": "#1A202C", "muted": "#4A5568", "primary": "#2B6CB0", "focus": "#2B6CB0",
        "success-bg": "#E6F4EA", "success-fg": "#1E5631", "success-bd": "#38A169",
        "warning-bg": "#FFF4E5", "warning-fg": "#7A3E00", "warning-bd": "#DD6B20",
        "danger-bg": "#FDECEC", "danger-fg": "#8B1A1A", "danger-bd": "#E53E3E",
        "info-bg": "#EBF4FF", "info-fg": "#1A4E8A", "info-bd": "#3182CE",
        "neutral-bg": "#EDF2F7", "neutral-fg": "#2D3748", "neutral-bd": "#A0AEC0",
        "row-alt": "#F7FAFC",
    },
    "dark": {
        "bg": "#0F1419", "surface": "#1A2129", "surface-2": "#232C36", "border": "#3A4654",
        "text": "#E6EDF3", "muted": "#A9B6C3", "primary": "#63B3ED", "focus": "#90CDF4",
        "success-bg": "#12301F", "success-fg": "#9AE6B4", "success-bd": "#48BB78",
        "warning-bg": "#3A2608", "warning-fg": "#FBD38D", "warning-bd": "#ED8936",
        "danger-bg": "#3B1416", "danger-fg": "#FEB2B2", "danger-bd": "#F56565",
        "info-bg": "#132A44", "info-fg": "#BEE3F8", "info-bd": "#4299E1",
        "neutral-bg": "#232C36", "neutral-fg": "#E2E8F0", "neutral-bd": "#718096",
        "row-alt": "#151B22",
    },
}

CHART_COLORS = {
    "APPROVED": {"light": "#2F855A", "dark": "#68D391"},
    "NEEDS_REVIEW": {"light": "#C05621", "dark": "#F6AD55"},
    "BLOCKED": {"light": "#C53030", "dark": "#FC8181"},
    "FAILED": {"light": "#702459", "dark": "#F687B3"},
    "IN_PROGRESS": {"light": "#2B6CB0", "dark": "#63B3ED"},
    "bar": {"light": "#2B6CB0", "dark": "#63B3ED"},
}


def default_theme() -> str:
    """Startup default derived from the (read-only) config.toml theme."""
    try:
        base = st.get_option("theme.base")
        if base in ("light", "dark"):
            return str(base)
        bg = (st.get_option("theme.backgroundColor") or "").lstrip("#")
        if len(bg) == 6:
            r, g, b = (int(bg[i:i + 2], 16) for i in (0, 2, 4))
            return "dark" if (0.2126 * r + 0.7152 * g + 0.0722 * b) < 128 else "light"
    except Exception:
        pass
    return "light"


def current_theme() -> str:
    value = st.session_state.get(THEME_STATE_KEY)
    return value if value in TOKENS else default_theme()


def render_theme_selector() -> str:
    """Sidebar radio group (native, keyboard-accessible selected state)."""
    if st.session_state.get(THEME_STATE_KEY) not in TOKENS:
        st.session_state[THEME_STATE_KEY] = default_theme()
    choice = st.radio(
        "Theme",
        options=list(THEME_LABELS),
        format_func=lambda key: THEME_LABELS[key],
        horizontal=True,
        key=THEME_STATE_KEY,
        help="Applies to this browser session only. The startup default comes from .streamlit/config.toml.",
    )
    return choice if choice in TOKENS else default_theme()


def _vars_css(theme: str) -> str:
    return ":root{" + "".join(f"--ap-{k}:{v};" for k, v in TOKENS[theme].items()) + "}"


_COMPONENT_CSS = """
.ap-sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.ap-muted{color:var(--ap-muted)}
.ap-badge{display:inline-flex;align-items:center;gap:.35em;border:1px solid;border-radius:999px;font-weight:600;line-height:1.2;white-space:nowrap}
.ap-badge--sm{font-size:.78rem;padding:.12em .55em}
.ap-badge--md{font-size:.9rem;padding:.2em .7em}
.ap-badge--lg{font-size:1.15rem;padding:.3em .9em}
.ap-badge__sym{font-weight:800}
.ap-badge--success{background:var(--ap-success-bg);color:var(--ap-success-fg);border-color:var(--ap-success-bd)}
.ap-badge--warning{background:var(--ap-warning-bg);color:var(--ap-warning-fg);border-color:var(--ap-warning-bd)}
.ap-badge--danger{background:var(--ap-danger-bg);color:var(--ap-danger-fg);border-color:var(--ap-danger-bd)}
.ap-badge--info{background:var(--ap-info-bg);color:var(--ap-info-fg);border-color:var(--ap-info-bd)}
.ap-badge--neutral{background:var(--ap-neutral-bg);color:var(--ap-neutral-fg);border-color:var(--ap-neutral-bd)}
.ap-kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,150px),1fr));gap:.75rem;margin:.25rem 0 1rem}
.ap-kpi{background:var(--ap-surface);border:1px solid var(--ap-border);border-left-width:4px;border-radius:.5rem;padding:.7rem .85rem;min-width:0}
.ap-kpi--success{border-left-color:var(--ap-success-bd)}.ap-kpi--warning{border-left-color:var(--ap-warning-bd)}
.ap-kpi--danger{border-left-color:var(--ap-danger-bd)}.ap-kpi--info{border-left-color:var(--ap-info-bd)}
.ap-kpi__label{font-size:.8rem;color:var(--ap-muted);font-weight:600;text-transform:uppercase;letter-spacing:.03em}
.ap-kpi__value{font-size:clamp(1.15rem,2.2vw,1.6rem);font-weight:700;color:var(--ap-text);overflow-wrap:anywhere}
.ap-kpi__note{font-size:.8rem;color:var(--ap-muted)}
.ap-table-wrap{overflow-x:auto;max-width:100%;border:1px solid var(--ap-border);border-radius:.5rem;margin:.25rem 0 1rem}
.ap-table-wrap:focus-visible{outline:3px solid var(--ap-focus);outline-offset:2px}
.ap-table{width:100%;border-collapse:collapse;font-size:.88rem;color:var(--ap-text);background:var(--ap-bg)}
.ap-table th{position:sticky;top:0;background:var(--ap-surface-2);color:var(--ap-text);text-align:left;font-weight:700;padding:.5rem .65rem;border-bottom:1px solid var(--ap-border);white-space:nowrap}
.ap-table td{padding:.45rem .65rem;border-bottom:1px solid var(--ap-border);white-space:nowrap;vertical-align:top}
.ap-table tbody tr:nth-child(even){background:var(--ap-row-alt)}
.ap-table .ap-wrap{white-space:normal;min-width:7rem;max-width:26rem;overflow-wrap:anywhere}
.ap-table .ap-num{text-align:right;font-variant-numeric:tabular-nums}
.ap-empty{padding:.75rem 1rem;border:1px dashed var(--ap-border);border-radius:.5rem;color:var(--ap-muted)}
.ap-decision{border:1px solid var(--ap-border);border-left-width:6px;border-radius:.6rem;padding:.85rem 1rem;margin:.5rem 0 1rem;background:var(--ap-surface)}
.ap-decision--success{border-left-color:var(--ap-success-bd)}.ap-decision--warning{border-left-color:var(--ap-warning-bd)}
.ap-decision--danger{border-left-color:var(--ap-danger-bd)}.ap-decision--info{border-left-color:var(--ap-info-bd)}
.ap-decision--neutral{border-left-color:var(--ap-neutral-bd)}
.ap-decision__title{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;font-weight:700;color:var(--ap-text)}
.ap-decision__body{margin-top:.4rem;color:var(--ap-text)}
.ap-pager{color:var(--ap-muted);font-size:.9rem;padding-top:.45rem}
.ap-preview{overflow:auto;max-height:80vh;border:1px solid var(--ap-border);border-radius:.5rem;background:var(--ap-surface-2);padding:.5rem}
.ap-preview:focus-visible{outline:3px solid var(--ap-focus);outline-offset:2px}
.ap-preview img{display:block;max-width:none;height:auto;margin:0 auto;background:#fff}
.ap-stage{display:flex;flex-wrap:wrap;gap:.5rem;align-items:baseline}
.ap-stage__num{font-variant-numeric:tabular-nums;color:var(--ap-muted);min-width:2.2rem}
"""

# Native Streamlit widgets follow config.toml (light). In dark mode we restyle
# them through public test ids / ARIA roles only.
_DARK_NATIVE_CSS = """
[data-testid="stApp"],[data-testid="stHeader"],[data-testid="stMain"]{background-color:var(--ap-bg);color:var(--ap-text);color-scheme:dark}
[data-testid="stSidebar"],[data-testid="stSidebarContent"]{background-color:var(--ap-surface);color:var(--ap-text)}
[data-testid="stApp"] :is(h1,h2,h3,h4,h5,h6,p,li,label,summary,strong,em,small,legend),
[data-testid="stApp"] [data-testid="stMarkdownContainer"],[data-testid="stApp"] [data-testid="stWidgetLabel"]{color:var(--ap-text)}
[data-testid="stApp"] [data-testid="stCaptionContainer"],[data-testid="stApp"] [data-testid="stCaptionContainer"] *{color:var(--ap-muted)}
[data-testid="stApp"] :is(input,textarea){background-color:var(--ap-surface-2);color:var(--ap-text);caret-color:var(--ap-text)}
[data-testid="stApp"] :is(input,textarea)::placeholder{color:var(--ap-muted);opacity:1}
[data-testid="stApp"] :is([data-baseweb="input"],[data-baseweb="base-input"],[data-baseweb="textarea"],[data-baseweb="select"]>div){background-color:var(--ap-surface-2);border-color:var(--ap-border);color:var(--ap-text)}
[data-testid="stApp"] [data-baseweb="select"] *{color:var(--ap-text)}
[data-baseweb="popover"] :is([role="listbox"],ul){background-color:var(--ap-surface);color:var(--ap-text)}
[data-baseweb="popover"] [role="option"]{color:var(--ap-text)}
[data-baseweb="popover"] [role="option"][aria-selected="true"],[data-baseweb="popover"] [role="option"]:hover{background-color:var(--ap-surface-2)}
[data-testid="stApp"] [data-testid="stExpander"] details{background-color:var(--ap-surface);border-color:var(--ap-border)}
[data-testid="stApp"] [data-testid="stExpander"] summary:hover{color:var(--ap-primary)}
[data-testid="stApp"] [data-testid="stForm"]{border-color:var(--ap-border)}
[data-testid="stApp"] [data-testid^="stBaseButton-secondary"],[data-testid="stApp"] [data-testid^="stBaseButton-tertiary"]{background-color:var(--ap-surface-2);color:var(--ap-text);border-color:var(--ap-border)}
[data-testid="stApp"] [data-testid^="stBaseButton-secondary"]:disabled{color:var(--ap-muted);opacity:.7}
[data-testid="stApp"] [data-testid^="stNumberInputStep"]{background-color:var(--ap-surface-2);color:var(--ap-text)}
[data-testid="stApp"] [data-testid="stFileUploaderDropzone"]{background-color:var(--ap-surface-2);color:var(--ap-text)}
[data-testid="stApp"] [role="tab"]{color:var(--ap-muted)}
[data-testid="stApp"] [role="tab"][aria-selected="true"]{color:var(--ap-text)}
[data-testid="stApp"] :is(pre,code){background-color:var(--ap-surface-2);color:var(--ap-text)}
[data-testid="stApp"] [data-testid="stMetricValue"],[data-testid="stApp"] [data-testid="stMetricLabel"]{color:var(--ap-text)}
[data-testid="stApp"] hr{border-color:var(--ap-border)}
[data-testid="stApp"] [data-testid="stAlert"],[data-testid="stApp"] [data-testid="stAlert"]>div{background-color:var(--ap-surface-2)!important;border:1px solid var(--ap-border)!important;color:var(--ap-text)!important}
[data-testid="stApp"] [data-testid="stAlert"] *{color:var(--ap-text)!important}
[data-testid="stHeader"] button,[data-testid="stSidebar"] button,[data-testid="stExpandSidebarButton"],[data-testid="stSidebarCollapseButton"]{color:var(--ap-text)!important;background-color:var(--ap-surface-2)!important;border:1px solid var(--ap-border)!important}
[data-testid="stHeader"] button svg,[data-testid="stSidebar"] button svg{fill:var(--ap-text)!important}
[data-testid="stApp"] [data-testid^="stDownloadButton"] button{background-color:var(--ap-surface-2);color:var(--ap-text);border-color:var(--ap-border)}
"""

_FOCUS_CSS = """
[data-testid="stApp"] :is(a,button,input,textarea,select,summary,[role="radio"],[role="tab"],[tabindex]):focus-visible{outline:3px solid var(--ap-focus);outline-offset:2px}
@media (max-width:640px){.ap-kpi{padding:.55rem .65rem}.ap-table{font-size:.82rem}}
"""


def apply_theme(theme: str) -> None:
    css = _vars_css(theme) + _COMPONENT_CSS + _FOCUS_CSS
    if theme == "dark":
        css += _DARK_NATIVE_CSS
    st.html(f"<style>{css}</style>")


def style_figure(fig: Any, theme: str, height: int = 300) -> Any:
    """Apply theme-consistent Plotly styling (pass ``theme=None`` to st.plotly_chart)."""
    tokens = TOKENS[theme]
    fig.update_layout(
        template="plotly_dark" if theme == "dark" else "plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=tokens["text"]),
        margin=dict(t=10, b=10, l=10, r=10),
        height=height,
        legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5),
    )
    return fig


def chart_color(key: str, theme: str) -> str:
    return CHART_COLORS.get(key, CHART_COLORS["bar"])[theme]
