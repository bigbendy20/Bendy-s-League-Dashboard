"""
Magazine layout primitives: hero banners, section cards, tables.

Runtime state (the loaded DataFrames, the resolved accent colours, the Riot
client and so on) is injected into this module's namespace by `app.py` on
every rerun — see `runtime.bind()`. That's deliberate: Streamlit re-executes
the whole script each interaction, so the alternative was threading two dozen
values through every function signature. The tradeoff is that these names
look undefined to a linter reading this file alone; `tools/check_bindings.py`
verifies at build time that every one of them is actually provided.
"""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from compat import FULL_WIDTH

import ddragon
import insights
import rank_history
import recap
import stats
from stats import *  # noqa: F403 - the stats layer is this app's vocabulary


# ==================== Magazine layout helpers ====================
# Defined here (before the onboarding form, which needs them too) rather
# than down with the other page helpers.
def render_hero(title: str, subtitle: str = "", champ: str | None = None, stats: list | None = None):
    """Big magazine-style hero header for the top of a page — bigger type, a
    larger portrait, and optional quick-glance stat chips (list of
    (label, value) tuples). Stays a transparent glass panel rather than an
    image-backed banner — a second full-size image here fought with the
    page's own full-bleed splash background in an earlier round. The eyebrow
    line shows your current ranked tier — the same signal driving the
    accent color — rather than a theme name."""
    # A page that names a champion (the deep-dive) shows that champion. The
    # site-level heroes show *you* — your League profile icon, resolved in
    # app.py because this also runs on the onboarding path, before there's a
    # summoner to read.
    icon_url = ddragon.hero_icon_url(champ, hero_icon_url, version)
    # Data Dragon occasionally 404s a brand-new profile icon id that hasn't
    # shipped to the CDN yet. A missing id is already handled upstream; this
    # covers the id being present but the *file* not existing, which upstream
    # can't detect without an extra request per page load.
    fallback_icon = ddragon.champion_icon_url(champ or HERO_CHAMPION, version)
    subtitle_html = f'<div class="hero-banner-subtitle">{subtitle}</div>' if subtitle else ""
    stats_html = ""
    if stats:
        chips = "".join(
            f'<div class="hero-stat-chip"><div class="chip-label">{label}</div>'
            f'<div class="chip-value">{value}</div></div>'
            for label, value in stats
        )
        stats_html = f'<div class="hero-stat-row">{chips}</div>'
    st.markdown(
        f"""<div class="hero-banner">
                <img class="hero-icon" src="{icon_url}"
                     onerror="this.onerror=null;this.src='{fallback_icon}';"/>
                <div class="hero-banner-body">
                    <div class="hero-banner-eyebrow">{rank_label}</div>
                    <div class="hero-banner-title">{title}</div>
                    {subtitle_html}
                    {stats_html}
                </div>
            </div>""",
        unsafe_allow_html=True,
    )


def section_card(key: str, title: str | None = None, subtitle: str = "", icon: str = "◆", featured: bool = False):
    """A themed 'magazine' card surface, used with `with section_card(...):`.
    Built on `st.container(border=True, key=...)` — a real bordered
    container, not an HTML div opened via st.markdown, since that older hack
    doesn't actually enclose native widgets (they render as siblings, not
    children). Streamlit gives every keyed element a `st-key-<key>` CSS
    class, which is what the .hero/.card rules in render_theme_css() target.
    `featured` cards get a bolder accent top border for the content that
    should draw the eye first on a page."""
    prefix = "card-feat-" if featured else "card-"
    container = st.container(border=True, key=f"{prefix}{key}")
    if title:
        with container:
            sub_html = f'<div class="card-subtitle">{subtitle}</div>' if subtitle else ""
            st.markdown(
                f'<div class="card-title"><span class="card-icon">{icon}</span>{title}</div>{sub_html}',
                unsafe_allow_html=True,
            )
    return container


PERCENT_COLUMNS = {
    "win_rate": "Win rate",
    "kill_participation": "Kill participation",
    "damage_share": "Damage share",
}


def percent_table(data: pd.DataFrame, **kwargs):
    """Render a table with rate columns shown as percentages.

    Uses Streamlit's `column_config` rather than formatting the values into
    strings, so the columns stay genuinely numeric — sorting a "62.5%"
    string column would sort lexically and put 9% after 80%."""
    config = {
        col: st.column_config.NumberColumn(label, format="%.1f%%")
        for col, label in PERCENT_COLUMNS.items()
        if col in data.columns
    }
    st.dataframe(data, **FULL_WIDTH, column_config=config, **kwargs)


def metric_grid(items: list, cols_per_row: int = 3):
    """Lay out (label, value) pairs as st.metric cards, wrapping to a new
    row every `cols_per_row` items — used inside narrower cards where a
    single wide st.columns(7)-style row wouldn't fit."""
    for i in range(0, len(items), cols_per_row):
        row_items = items[i:i + cols_per_row]
        cols = st.columns(cols_per_row)
        for col, (label, value) in zip(cols, row_items):
            col.metric(label, value)

