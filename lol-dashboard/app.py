import os
from pathlib import Path

import pandas as pd

import compat
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from compat import FULL_WIDTH

import auth
import env_file
import ddragon
import insights
import profiles
import rank_history
import recap
from riot_client import RiotClient
from themes import DEFAULT_ACCENT, get_tier_colors, hex_to_rgb, readable_accents
import stats
from stats import *  # noqa: F403 - the stats layer is this app's vocabulary

import components
import layout
import runtime
import store
import theme_css
import views
from layout import metric_grid, percent_table, render_hero, section_card

# One loader for the app and the CLI scripts. They read the same file, and
# the app running from a different working directory than the jobs is exactly
# how they came to disagree about whether the key existed.
#
# Anchored to *this* file rather than to `env_file`'s own location, which
# matters for more than tidiness: the startup tests deliberately point
# `__file__` at a throwaway directory so that running them can't read — or
# write — the developer's real `.env`. Resolving from the loader's location
# would have quietly defeated that, and did: two startup tests started
# passing or failing according to what happened to be in the local config.
env_file.load(env_file.path_for(__file__))

APP_TITLE = "Bendy's League Board"
# The browser tab keeps the site's name; the hero card is retitled per profile
# further down, once the active profile is known. `PROFILE_TITLE` is what the
# pages actually render — a fixed name at the top of someone else's page reads
# like you're still looking at your own.
FAVICON_PATH = os.path.join(os.path.dirname(__file__), "assets", "favicon.png")

st.set_page_config(
    page_title=APP_TITLE,
    page_icon=FAVICON_PATH if os.path.exists(FAVICON_PATH) else None,
    layout="wide",
    # Nothing renders into the sidebar any more — navigation is top tabs and
    # the controls live in a bar under the header. Collapsing it explicitly
    # is belt-and-braces: Streamlit hides an empty sidebar on its own, but
    # this also keeps it out of the way on versions where top navigation
    # briefly also drew itself in the sidebar.
    initial_sidebar_state="collapsed",
)

# ==================== Light / dark toggle ====================
# Independent of the region/skin-line accent theme. Note: Streamlit's own
# native chrome (sidebar, buttons, built-in dataframe styling) is fixed to
# the dark base set in .streamlit/config.toml and can't be changed at
# runtime, so this toggle flips the custom-styled surfaces (background,
# cards, headers, charts) — the truest "light mode" achievable without
# restarting the app.
#
# The value is read here (CSS is built from it further down) but the toggle
# *widget* renders later, in the top control bar. Reading session_state
# before the widget exists is fine: flipping the toggle triggers a rerun, so
# the CSS picks up the new value on the very next pass.
if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = True
dark_mode = st.session_state.dark_mode
PLOT_TEMPLATE = "plotly_dark" if dark_mode else "plotly_white"
TEXT_COLOR = "#F4F4F7" if dark_mode else "#15151C"
GRID_COLOR = "rgba(255,255,255,0.10)" if dark_mode else "rgba(0,0,0,0.10)"


@st.cache_data(ttl=86400)
def get_ddragon_version() -> str:
    return ddragon.get_latest_version()


@st.cache_data(ttl=86400)
def get_items_catalog(version: str) -> dict:
    return ddragon.get_items(version)


@st.cache_data(ttl=86400)
def get_runes_catalog(version: str) -> dict:
    return ddragon.get_runes(version)


@st.cache_data(ttl=86400)
def get_summoner_spells_catalog(version: str) -> dict:
    return ddragon.get_summoner_spells(version)


@st.cache_data(ttl=86400)
def get_champions_catalog(version: str) -> dict:
    """Numeric championId -> champion id/name; needed to translate
    champion-mastery-v4's numeric ids into the names used everywhere else."""
    return ddragon.get_champions(version)


version = get_ddragon_version()


def build_plot_template(accent: str, accent2: str) -> go.layout.Template:
    """A themed plotly Template — transparent paper/plot backgrounds (so
    charts sit on the card's own glass surface instead of drawing their own
    box), the current accent colors as the default trace colorway, and
    grid/text colors matching the rest of the UI. Built from scratch with
    explicit dicts rather than cloning plotly's built-in template (safer
    without a way to click-test the result). Called twice per run: once with
    safe defaults before any rank data exists, and again with the real
    rank-tier accent once it's known (see below)."""
    return go.layout.Template(layout=go.Layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        colorway=[accent, accent2, "#2DD4BF", "#FB7185", "#F4C542", "#7C9CFF"],
        font=dict(color=TEXT_COLOR, family="Inter, sans-serif"),
        xaxis=dict(gridcolor=GRID_COLOR, zerolinecolor=GRID_COLOR, linecolor=GRID_COLOR),
        yaxis=dict(gridcolor=GRID_COLOR, zerolinecolor=GRID_COLOR, linecolor=GRID_COLOR),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
    ))


# ==================== Visual identity: rank-tier accent + your hero pick ====================
# Accent colors derive from your current ranked tier once it's known further
# down (a meaningful signal, not a random reshuffle); hero art is a single
# champion you choose once in Settings, not auto-rotated. Both start from
# safe defaults here since no rank data exists yet at this point in the
# script — the accent gets upgraded (and this CSS re-rendered) right after
# your ranked entry loads, further down.
accent, accent2 = DEFAULT_ACCENT
accent_rgb = hex_to_rgb(accent)
accent2_rgb = hex_to_rgb(accent2)
# Contrast-corrected variants for anything rendered as *text*. Decoration
# (borders, glows, chart lines) keeps the raw accent — see themes.py.
accent_text, accent2_text = readable_accents(accent, accent2, dark_mode)
# Background art. Empty (or "Auto") means "whoever I've played most", which
# is resolved once match data loads — the same two-stage shape as the accent
# colour, and for the same reason: nothing is known yet at this point in the
# script, so the first paint uses a safe fallback and the real value wins in
# the second CSS injection further down.
#
# Riot's public API has no endpoint for the profile background you set in the
# client; summoner-v4 exposes profileIconId and nothing else visual. Most
# played is the closest honest stand-in.
HERO_SETTING = os.getenv("HERO_CHAMPION", "").strip()
HERO_AUTO = "Auto (most played)"
HERO_FALLBACK = "Kindred"
hero_is_auto = not HERO_SETTING or HERO_SETTING == HERO_AUTO
HERO_CHAMPION = HERO_FALLBACK if hero_is_auto else HERO_SETTING
hero_url = ddragon.champion_splash_url(HERO_CHAMPION, 0)
# Before summoner data exists the hero circle falls back to champion art;
# phase 2 swaps in the real profile icon. Bound rather than looked up inside
# render_hero() because that function runs on the onboarding path too, long
# before there's a summoner to read.
hero_icon_url = ddragon.champion_icon_url(HERO_CHAMPION, version)
rank_label = "Unranked"

PLOT_TEMPLATE = build_plot_template(accent, accent2)

# First pass with the neutral defaults, so the onboarding form and the
# control bar are styled before any rank data exists. Re-rendered further
# down with the real rank-tier accent once `solo` is known.
st.markdown(
    theme_css.render_theme_css(
        accent, accent2, accent_rgb, accent2_rgb, hero_url, dark_mode,
        accent_text, accent2_text,
    ),
    unsafe_allow_html=True,
)


# ==================== Config and profiles ====================
# The API key is server-side only — it comes from the environment and is
# never sent to a browser. Everything else is *per profile*.
#
# Two modes, deliberately both supported during the migration to a hosted
# site. With a store configured, profiles come from it and several friends
# can be viewed. With nothing but a `.env`, `bootstrap_from_env` produces a
# single profile and the app behaves exactly as the local version always
# has — so the offline path keeps working rather than requiring a database
# to run anything at all.
API_KEY = os.getenv("RIOT_API_KEY", "").strip()

# Whatever `DATABASE_URL` names — Postgres when deployed, a local SQLite file
# otherwise. This was `FileStore(<app dir>/data/profiles)`, hardcoded, which
# meant the hosted site would have read an empty directory on an ephemeral
# disk and shown everyone's profile as empty while the database sat there
# full. `base_dir` is passed so a relative path resolves next to the app, and
# so the startup tests can point the whole thing at a sandbox.
data_store = store.open_store(base_dir=os.path.dirname(os.path.abspath(__file__)))
registered = data_store.list_profiles()
if not registered:
    registered = profiles.bootstrap_from_env(os.environ)

# ==================== Access control ====================
# Two separate questions — see auth.py. Streamlit's OIDC proves you hold a
# Google account; the allow-list decides whether it's one of ours. Signing in
# with Google alone proves only that you have a Gmail address, which is not
# a meaningful gate.
#
# The *decision* is made here, before any data is loaded or fetched, so a
# rejected visitor never costs API budget. The *rendering* of the sign-in
# screen has to wait until BASE_BINDING exists a little further down —
# splitting the two is what keeps the check early without needing the whole
# UI to be ready.
ALLOWLIST = auth.parse_allowlist(
    st.secrets.get("allowed_emails") if hasattr(st, "secrets") else None
)
if auth.local_bypass(os.environ):
    # Local development only, and explicitly opted into. Never inferred from
    # "no allow-list configured" — a missing secret must lock the door, not
    # remove it.
    access = {"state": "allowed", "email": "", "message": ""}
else:
    access = auth.gate(getattr(st, "user", None), ALLOWLIST)

signed_in_email = access["email"] if access["state"] == "allowed" else ""

# `?profile=<puuid>` makes a friend's page linkable; without one, resolution
# falls through to the signed-in user's own profile.
requested_profile = st.query_params.get("profile")
active_profile = profiles.resolve_active(
    registered, requested_puuid=requested_profile, signed_in_email=signed_in_email
) or {}

GAME_NAME = (active_profile.get("game_name") or "").strip()
TAG_LINE = (active_profile.get("tag_line") or "").strip().lstrip("#")
PLATFORM_REGION = (active_profile.get("platform_region") or "na1").strip()
CONTINENTAL_REGION = (active_profile.get("continental_region") or "americas").strip()
viewing_own_profile = profiles.is_own_profile(active_profile, signed_in_email)

# What the hero card says. Whoever's page you're on, their Riot ID is the
# title — the site name stays in the browser tab. On a multi-profile board a
# fixed "Bendy's League Board" above someone else's stats actively misleads:
# the numbers change when you switch profiles and the heading doesn't.
PROFILE_TITLE = f"{GAME_NAME}#{TAG_LINE}" if GAME_NAME and TAG_LINE else APP_TITLE

try:
    MATCH_HISTORY_TARGET = int(os.getenv("MATCH_HISTORY_TARGET", "1000"))
except ValueError:
    MATCH_HISTORY_TARGET = 1000

# The climb goal lives on the profile record, not in the environment. It was
# `GOAL_TIER`/`GOAL_RANK` from `.env` — one goal for the whole site, which on
# a shared board meant everyone saw Bendy's target on their own page, and on
# Streamlit Cloud meant nobody could change it at all, since `.env` isn't
# writable there and wouldn't survive a restart if it were.
GOAL_TIER = (active_profile.get("goal_tier") or "").strip().upper()
GOAL_RANK = (active_profile.get("goal_rank") or "").strip().upper()


def update_env_value(key: str, value: str) -> None:
    """Persist a single setting back to .env (in-place, preserving every
    other line) so both the onboarding form and the Settings panel actually
    stick across restarts instead of just overriding it for this session."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = f.readlines()
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)
    # Apply it to this process too. `env_file.load(env_file.path_for(__file__))` deliberately never
    # overwrites a variable that's already set, so without this line a *new*
    # key written by the Settings panel would sit in the file, correct and
    # ignored, until the app was restarted — the first-run case worked only
    # because there was nothing to overwrite.
    os.environ[key] = value


# Platform region -> continental routing region (needed for match history).
PLATFORM_TO_CONTINENTAL = {
    "na1": "americas", "br1": "americas", "la1": "americas", "la2": "americas", "oc1": "americas",
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe",
    "kr": "asia", "jp1": "asia",
}


def render_onboarding_form():
    """Shown instead of the dashboard when no account is configured yet —
    the whole point of this is that a friend downloading the folder never
    has to open a text file. Whatever they submit gets written to their own
    local .env, then the app reruns and loads normally from here on."""
    render_hero(APP_TITLE, "Let's get you set up")
    with section_card(
        "onboarding", "One-time setup",
        "This saves to a local `.env` file on this computer only — nothing is sent "
        "anywhere except Riot's own API.",
        icon="🛠️", featured=True,
    ):
        st.markdown(
            "Need an API key? Get a free one at "
            "[developer.riotgames.com](https://developer.riotgames.com/) — sign in, click "
            "**Generate API Key** under Development API Key. It expires every 24 hours; "
            "there's a spot to refresh it later without redoing this whole form."
        )
        with st.form("onboarding_form"):
            game_name = st.text_input("Riot ID — name part", placeholder="e.g. Faker")
            tag_line = st.text_input("Riot ID — tag part (no #)", placeholder="e.g. ChOmP")
            api_key = st.text_input("Riot API key", placeholder="RGAPI-...", type="password")
            platform_region = st.selectbox(
                "Region", list(PLATFORM_TO_CONTINENTAL.keys()), index=0,
                help="The server your account plays on (na1, euw1, kr, etc.).",
            )
            submitted = st.form_submit_button("Save & continue", type="primary")

    if submitted:
        if not game_name.strip() or not tag_line.strip() or not api_key.strip():
            st.error("All three fields are required.")
            return
        update_env_value("RIOT_GAME_NAME", game_name.strip())
        update_env_value("RIOT_TAG_LINE", tag_line.strip().lstrip("#"))
        update_env_value("RIOT_API_KEY", api_key.strip())
        update_env_value("PLATFORM_REGION", platform_region)
        update_env_value("CONTINENTAL_REGION", PLATFORM_TO_CONTINENTAL.get(platform_region, "americas"))
        st.success("Saved! Loading your dashboard...")
        st.rerun()


# ---- Phase 1 binding ----
# The onboarding form and the control bar both call into `layout` and
# `components`, and they render *before* any match data exists. So the
# theme/identity half of the runtime state has to be bound here rather than
# waiting for the single bind before navigation — otherwise the very first
# thing a new user sees raises NameError on `HERO_CHAMPION`.
BASE_BINDING = dict(
    APP_TITLE=APP_TITLE, PROFILE_TITLE=APP_TITLE,
    HERO_CHAMPION=HERO_CHAMPION, hero_icon_url=hero_icon_url,
    version=version,
    accent=accent, accent2=accent2, accent_rgb=accent_rgb, accent2_rgb=accent2_rgb,
    accent_text=accent_text, accent2_text=accent2_text, rank_label=rank_label,
    dark_mode=dark_mode, PLOT_TEMPLATE=PLOT_TEMPLATE,
    TEXT_COLOR=TEXT_COLOR, GRID_COLOR=GRID_COLOR,
    update_env_value=update_env_value,
    get_champions_catalog=get_champions_catalog, get_items_catalog=get_items_catalog,
    get_runes_catalog=get_runes_catalog,
    get_summoner_spells_catalog=get_summoner_spells_catalog,
    render_hero=render_hero, section_card=section_card,
    percent_table=percent_table, metric_grid=metric_grid,
    # components.EXPORTS is frozen at import time on purpose — see the note at
    # the bottom of components.py. Deriving it from dir() here re-read the names
    # bind() had injected on the previous rerun.
    **{name: getattr(components, name) for name in components.EXPORTS},
)

# The access decision was made above; this is where it can finally be shown.
# Nothing between there and here touches Riot or the store, so a rejected
# visitor has cost nothing.
if access["state"] != "allowed":
    with runtime.render_lock:
        runtime.bind(**BASE_BINDING)
        render_hero(APP_TITLE, access["message"])
        if access["state"] == "anonymous" and hasattr(st, "login"):
            # One button per configured provider. Which providers exist is
            # deployment config — the group isn't all on one email host, and
            # the allow-list matches addresses rather than issuers, so this
            # needed no change to any access rule.
            for provider, label in auth.sign_in_options(getattr(st, "secrets", {})):
                st.button(
                    label, type="primary", key=f"signin-{provider or 'default'}",
                    on_click=(lambda p=provider: st.login(p) if p else st.login()),
                )
        elif access["state"] == "denied" and hasattr(st, "logout"):
            st.button("Sign in with a different account", on_click=st.logout)
    st.stop()

if not API_KEY or API_KEY.startswith("RGAPI-xxxx") or not GAME_NAME or not TAG_LINE:
    # Bind and render together under the lock. `st.stop()` raises, and the
    # `with` releases correctly on the way out — an acquire/release pair
    # around this would deadlock every subsequent viewer.
    with runtime.render_lock:
        runtime.bind(**BASE_BINDING)
        render_onboarding_form()
    st.stop()


@st.cache_resource
def get_client() -> RiotClient:
    return RiotClient(API_KEY, CONTINENTAL_REGION, PLATFORM_REGION)


client = get_client()

# ==================== Top control bar ====================
# Everything that used to live in the sidebar. This renders before
# `pg.run()` at the bottom of the file, so it sits as a header strip above
# whichever tab is open — no sidebar anywhere in the app now.
ctrl_account, ctrl_profile, ctrl_refresh, ctrl_settings, ctrl_display = (
    st.columns([3.6, 1.6, 1.4, 1.3, 1.3])
)

with ctrl_account:
    st.markdown(
        f'<div class="control-account"><b>{GAME_NAME}#{TAG_LINE}</b>'
        f'<span class="control-meta">{PLATFORM_REGION.upper()} · {CONTINENTAL_REGION} · '
        f'{len(st.session_state.get("df", [])):,} matches · '
        f'updated {relative_time(st.session_state.get("last_updated"))}'
        f'{" · " + st.session_state.poll_status if st.session_state.get("poll_status") else ""}'
        f'</span></div>',
        unsafe_allow_html=True,
    )

with ctrl_refresh:
    refresh_clicked = st.button("↻ Refresh", type="primary", **FULL_WIDTH)

# Only worth showing once there's more than one person to switch between —
# in local mode this is a single profile and the control would be noise.
if len(registered) > 1:
    with ctrl_profile:
        labels = {p["puuid"]: (p.get("display_name") or profiles.riot_id(p))
                  for p in registered}
        current = active_profile.get("puuid")
        options = list(labels)
        chosen = st.selectbox(
            "Profile", options, index=options.index(current) if current in options else 0,
            format_func=lambda pid: labels.get(pid, pid),
            label_visibility="collapsed",
        )
        if chosen != current:
            # Through the URL rather than session state, so the page you're
            # looking at is always the page you can copy and send.
            st.query_params["profile"] = chosen
            st.rerun()

with ctrl_display:
    with st.popover("Display", **FULL_WIDTH):
        # `key="dark_mode"` writes straight to the session_state value read
        # near the top of the file, so no separate assignment is needed here.
        st.toggle("Dark mode", key="dark_mode")
        st.toggle(
            "Auto-refresh (5 min)", key="auto_refresh",
            help="Checks every 5 minutes for games you've finished and whether "
                 "you're in one now. Each check is a single API call — a full "
                 "reload only happens when there's actually something new. "
                 "Note that dev API keys expire after 24 hours, so leaving this "
                 "on overnight will ask you for a fresh key in the morning.",
        )
        use_cache = st.checkbox(
            "Use cached matches", value=True,
            help="Uncached full pulls are paced to respect Riot's dev-key rate limit — "
                 "hundreds of matches can take several minutes.",
        )

with ctrl_settings:
    with st.popover("Settings", **FULL_WIDTH):
        st.caption("Hero art")
        # Sourced from champions already in your match history — guarantees
        # a real, valid Data Dragon splash/icon rather than a free-text
        # champion name that might be misspelled. Uses .get() with a safe
        # default since this runs before the session_state defaults below
        # are set up — on a first-ever run "df" doesn't exist yet.
        _df_so_far = st.session_state.get("df", pd.DataFrame())
        played_for_picker = sorted(_df_so_far["champion"].dropna().unique()) \
            if not _df_so_far.empty else []
        current = HERO_AUTO if hero_is_auto else HERO_SETTING
        if current not in played_for_picker and not hero_is_auto:
            played_for_picker = sorted(set(played_for_picker) | {current})
        # Auto first so it reads as the default rather than an escape hatch.
        options = [HERO_AUTO] + played_for_picker
        new_hero = st.selectbox(
            "Background art", options, index=options.index(current) if current in options else 0,
            help="Auto follows whichever champion you've played most in the loaded "
                 "history. Riot doesn't publish the background you set on your League "
                 "profile, so this is the closest stand-in — pick a champion to pin it.",
        )
        if new_hero != current:
            update_env_value("HERO_CHAMPION", "" if new_hero == HERO_AUTO else new_hero)
            st.success("Saved — reloading...")
            st.rerun()

        st.divider()
        st.caption("Data")
        new_target = st.number_input(
            "Match history target",
            min_value=50, max_value=5000, step=50,
            value=MATCH_HISTORY_TARGET,
            help="How many recent matches to pull per refresh. Saved to .env.",
        )
        if st.button("Save match target"):
            update_env_value("MATCH_HISTORY_TARGET", str(int(new_target)))
            st.success("Saved — click Refresh to apply.")

        st.divider()
        st.caption("Climb goal")
        goal_tier_options = rank_history.TIER_ORDER
        goal_tier_index = goal_tier_options.index(GOAL_TIER) if GOAL_TIER in goal_tier_options else 3  # GOLD
        new_goal_tier = st.selectbox(
            "Goal tier", goal_tier_options, index=goal_tier_index,
            format_func=lambda t: t.title(),
        )
        if new_goal_tier in rank_history.APEX_TIERS:
            new_goal_rank = ""
            st.caption("Master and above don't have divisions.")
        else:
            division_options = list(rank_history.DIVISION_ORDER.keys())
            division_index = division_options.index(GOAL_RANK) if GOAL_RANK in division_options else 3  # IV
            new_goal_rank = st.selectbox("Goal division", division_options, index=division_index)
        if not viewing_own_profile:
            st.caption(
                f"This is {GAME_NAME}'s goal. You can only change your own — "
                "switch to your profile to set yours."
            )
        elif st.button("Save climb goal"):
            # Written to the profile record, so it follows the player rather
            # than the deployment, and survives a Streamlit Cloud restart —
            # `.env` is neither writable nor persistent there.
            updated = dict(active_profile)
            updated["goal_tier"] = new_goal_tier
            updated["goal_rank"] = new_goal_rank
            data_store.upsert_profile(updated)
            st.success("Saved.")
            st.rerun()

for key, default in [
    ("df", pd.DataFrame()),
    ("league_entries", []),
    ("summoner", {}),
    ("mastery", []),
    ("last_updated", None),
    ("auto_refresh", False),
    ("loaded_match_ids", ()),   # what the cheap poll compares against
    ("poll_status", ""),        # last poll outcome, shown in the control bar
    ("live_game", None),
    ("live_game_checked", False),
    ("loaded_once", False),
    ("puuid", None),
    ("openings_cache", {}),
    ("gold_cache", {}),
    ("objectives_cache", {}),
    ("gold_diff_cache", {}),
    ("cs_diff_cache", {}),
    ("skill_order_cache", {}),
    ("wp_table", {}),        # empirical win-probability lookup from your own games
    ("wp_series", {}),       # match_id -> [(minute, team gold diff)]
    ("even_cache", {}),      # champion -> {match_id: was the game even at 15?}
    ("heatmap_cache", {}),
    ("recap_bytes", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default


def fetch_everything():
    """Populate the session from the store, falling back to the API.

    **The store is the source of truth for matches, and it is read in full.**
    This used to fetch from Riot on every load, which was fine for a local
    single-user app and wrong for this one in two ways. It capped the site at
    whatever Riot still *lists* — measured at 620 ids, about six months —
    while the database holds 5,766 games back to September 2024, including
    1,858 that Riot will no longer enumerate at all. And it put a burst of
    API calls behind every page view by every friend, on a shared key.

    So the deal is: the refresher writes, the site reads, and nothing ever
    deletes. History only grows from here.

    The API is still used for the three things that are *current* rather than
    historical — ranked standing, profile icon, mastery — and each failure is
    swallowed, because none of them should be able to blank a page that has
    thousands of games ready to render.
    """
    try:
        puuid = active_profile.get("puuid")
        if not puuid:
            # Only on first run for a profile that was bootstrapped from .env
            # and never seeded. Everyone else already has one on record, and
            # resolving it deliberately doesn't need the network.
            puuid = client.get_puuid(GAME_NAME, TAG_LINE)
        st.session_state.puuid = puuid
        # `bootstrap_from_env` can't know the puuid — that needs an API call,
        # and profile resolution deliberately doesn't depend on the network.
        # This is where the bootstrap profile becomes a real, addressable one.
        data_store.upsert_profile(profiles.make_profile(
            puuid=puuid,
            game_name=GAME_NAME,
            tag_line=TAG_LINE,
            platform_region=PLATFORM_REGION,
            continental_region=CONTINENTAL_REGION,
            display_name=active_profile.get("display_name") or GAME_NAME,
            email=active_profile.get("email"),
        ))
        # Everything stored, with no limit. `load_matches` already returns the
        # parsed frame sorted by date, so there's nothing to rebuild.
        stored = data_store.load_matches(puuid)
        if stored.empty:
            # A profile with nothing collected yet — first run, or someone
            # added to the roster since the last refresh. Fetch enough to show
            # something now; the refresher fills in the rest within minutes.
            matches = client.fetch_recent_matches(
                puuid, count=MATCH_HISTORY_TARGET, use_cache=use_cache
            )
            stored = build_dataframe(matches, puuid)
            if not stored.empty:
                data_store.save_matches(puuid, stored.to_dict("records"))
        st.session_state.df = stored

        # Current standing, not history. Each of these is swallowed
        # individually: a rate-limited or expired key should cost the rank
        # badge and the avatar, not the entire page.
        try:
            entries = client.get_league_entries(puuid)
            st.session_state.league_entries = entries
            if entries:
                rank_history.log_snapshot(entries)
                data_store.save_rank_snapshot(puuid, entries)
        except Exception:
            st.session_state.league_entries = []

        try:
            st.session_state.summoner = client.get_summoner(puuid)
        except Exception:
            st.session_state.summoner = {}
        # Mastery is a nice-to-have, and get_champion_mastery() already
        # swallows its own errors — so a failure here leaves the list empty
        # rather than taking down the whole load.
        st.session_state.mastery = client.get_champion_mastery(puuid)
        st.session_state.last_updated = compat.utcnow()
        return True, None
    except Exception as e:
        return False, str(e)


if refresh_clicked or not st.session_state.loaded_once:
    with st.spinner("Loading match history..."):
        ok, err = fetch_everything()
    st.session_state.loaded_once = True
    if not ok:
        if "401" in err:
            st.error("401 Unauthorized — your API key is missing, mistyped, or expired.")
            st.caption(
                "Dev keys expire every 24 hours. Generate a fresh one at "
                "[developer.riotgames.com](https://developer.riotgames.com/) "
                "(sign in → **Generate API Key**), then paste it below — no need to "
                "find or edit `.env` yourself."
            )
            with st.form("refresh_key_form"):
                new_key = st.text_input("New API key", placeholder="RGAPI-...", type="password")
                key_submitted = st.form_submit_button("Save & retry", type="primary")
            if key_submitted and new_key.strip():
                update_env_value("RIOT_API_KEY", new_key.strip())
                get_client.clear()  # drop the cached client so it picks up the new key
                st.rerun()
        elif "404" in err:
            st.error(
                "404 Not Found — double check RIOT_GAME_NAME / RIOT_TAG_LINE / "
                "CONTINENTAL_REGION in `.env`."
            )
        else:
            st.error(f"Fetch failed: {err}")

all_df = st.session_state.df
# What the five-minute poll diffs against. Derived here, from whatever is
# actually loaded, rather than inside fetch_everything() — a fetch that fails
# or is skipped would otherwise leave this empty, and an empty baseline makes
# every poll report every game as new and trigger a full refresh. Keeping it
# next to the data it describes means the two can't disagree.
st.session_state.loaded_match_ids = tuple(all_df.get("match_id", []))
league_entries = st.session_state.league_entries
summoner = st.session_state.summoner
puuid = st.session_state.puuid
mastery = st.session_state.mastery

if all_df.empty:
    st.info("No match data loaded yet. Click **↻ Refresh** at the top of the page.")
    st.stop()

# ==================== The one place "your stats" gets defined ====================
# Everything downstream — every page, chart, tip, radar and headline — reads
# `df`, which is standard Summoner's Rift only (normal draft/blind/quickplay
# and ranked). ARAM, Arena, Swiftplay and rotating modes are split off into
# `other_modes_df` and shown on their own tab.
#
# Doing it here rather than per-page is the point: the alternative is thirty
# call sites each remembering to filter, and the one that forgets produces a
# number nobody can explain. `rift_only()` already existed for the radar for
# exactly this reason — this generalises it to the whole app.
df = core_only(all_df)
other_modes_df = non_core_only(all_df)

if df.empty:
    st.warning(
        f"None of your {len(all_df)} loaded games are standard Summoner's Rift "
        "(normal or ranked), so the main pages have nothing to show. "
        "**Other Modes** has the rest."
    )

solo = next((e for e in league_entries if e.get("queueType") == "RANKED_SOLO_5x5"), None)
lp_hist = rank_history.load_history("RANKED_SOLO_5x5")
# Recommendations are ranked-only regardless of the queue filter —
# tilt/patch/time-of-day patterns are a lot noisier (and less meaningful)
# mixed in with ARAM/normals, so this scopes to Ranked Solo/Duo + Flex.
ranked_df = df[df["queue_category"] == "Ranked"]

# Now that your ranked entry has loaded, upgrade the accent colors + chart
# theme from the safe defaults set earlier to the real tier-derived ones,
# and re-render the CSS — a second <style> block simply wins the cascade
# for the colors that changed, since it's appended later in the page.
rank_label = f"{solo['tier'].title()} {solo['rank']}" if solo else "Unranked"
accent, accent2 = get_tier_colors(solo["tier"] if solo else None)
accent_rgb = hex_to_rgb(accent)
accent2_rgb = hex_to_rgb(accent2)
accent_text, accent2_text = readable_accents(accent, accent2, dark_mode)

# Same upgrade for the background art: on Auto it becomes your most-played
# champion now that there's a history to count. Recomputed before the second
# CSS injection below so the new splash rides along with the accent change.
if hero_is_auto:
    HERO_CHAMPION = most_played_champion(df) or HERO_FALLBACK
    hero_url = ddragon.champion_splash_url(HERO_CHAMPION, 0)

# The hero circle becomes your actual League profile icon once summoner-v4
# has answered. Falls back to champion art if the call failed or the icon id
# is missing, so the hero never renders as a broken image.
icon_id = (summoner or {}).get("profileIconId")
hero_icon_url = ddragon.site_icon_url(icon_id, HERO_CHAMPION, version)
st.markdown(
    theme_css.render_theme_css(
        accent, accent2, accent_rgb, accent2_rgb, hero_url, dark_mode,
        accent_text, accent2_text,
    ),
    unsafe_allow_html=True,
)
PLOT_TEMPLATE = build_plot_template(accent, accent2)

# ==================== Queue filter (global) ====================
# Only ever Ranked / Normal now, because `df` is already core-only — the
# ARAM and Arena entries this used to offer moved to the Other Modes tab.
# Built from what's actually present rather than hardcoded, so someone with
# no normal games doesn't get an option that yields an empty page.
category_counts = df["queue_category"].value_counts()
queue_options = ["All"] + category_counts.index.tolist()

filter_col, rank_col = st.columns([5, 2])
with filter_col:
    queue_filter = st.radio(
        "Queue", queue_options, index=0, horizontal=True, label_visibility="collapsed",
    )
with rank_col:
    profile_icon = (
        f'<img src="{ddragon.profile_icon_url(summoner.get("profileIconId", 0), version)}" '
        f'width="34" style="border-radius:50%;vertical-align:middle;margin-right:8px;'
        f'border:2px solid rgba({accent_rgb},0.5);"/>'
        if summoner else ""
    )
    st.markdown(
        f"""<div style="text-align:right;">{profile_icon}
            <span class="hero-stat-chip" style="display:inline-block;padding:4px 14px;">
            <span class="chip-label">Rank</span>
            <span class="chip-value" style="font-size:1rem;">{rank_label}</span>
            </span></div>""",
        unsafe_allow_html=True,
    )

filtered_df = df if queue_filter == "All" else df[df["queue_category"] == queue_filter]


# ==================== Phase 2 binding ====================
# Now that data has loaded and the real rank-tier accent is known, re-bind
# everything. Phase 1 above covers the pre-data UI; this covers the pages.
# See runtime.py for why this is a binding rather than function arguments.
#
# Built as a dict rather than bound immediately, and merged over BASE_BINDING
# rather than layered on top of it, because another session may have rebound
# the module globals since phase 1 ran. Rebinding the complete set in one call
# — inside the lock, immediately before rendering — is what makes a page
# render see one consistent profile rather than a mix of two.
PAGE_BINDING = dict(
    # Data
    df=df, filtered_df=filtered_df, ranked_df=ranked_df,
    # `all_df` is every loaded game including the non-core modes. Only the
    # Raw Data export and the Other Modes tab should touch it.
    all_df=all_df, other_modes_df=other_modes_df,
    solo=solo, lp_hist=lp_hist, summoner=summoner, mastery=mastery,
    puuid=puuid, client=client, version=version, use_cache=use_cache,
    queue_filter=queue_filter,
    # Identity / config
    APP_TITLE=APP_TITLE, PROFILE_TITLE=PROFILE_TITLE,
    GAME_NAME=GAME_NAME, TAG_LINE=TAG_LINE,
    HERO_CHAMPION=HERO_CHAMPION, hero_icon_url=hero_icon_url,
    GOAL_TIER=GOAL_TIER, GOAL_RANK=GOAL_RANK,
    MATCH_HISTORY_TARGET=MATCH_HISTORY_TARGET,
    update_env_value=update_env_value,
    # Theme
    accent=accent, accent2=accent2, accent_rgb=accent_rgb, accent2_rgb=accent2_rgb,
    accent_text=accent_text, accent2_text=accent2_text, rank_label=rank_label,
    dark_mode=dark_mode, PLOT_TEMPLATE=PLOT_TEMPLATE,
    TEXT_COLOR=TEXT_COLOR, GRID_COLOR=GRID_COLOR,
    # Cached Data Dragon lookups
    get_champions_catalog=get_champions_catalog, get_items_catalog=get_items_catalog,
    get_runes_catalog=get_runes_catalog,
    get_summoner_spells_catalog=get_summoner_spells_catalog,
    # Cross-module UI helpers
    render_hero=render_hero, section_card=section_card,
    percent_table=percent_table, metric_grid=metric_grid,
    # components.EXPORTS is frozen at import time on purpose — see the note at
    # the bottom of components.py. Deriving it from dir() here re-read the names
    # bind() had injected on the previous rerun.
    **{name: getattr(components, name) for name in components.EXPORTS},
)

# ==================== Auto-refresh ====================
# Two independent pollers on a five-minute timer. `st.fragment(run_every=...)`
# re-runs *only* the decorated function on that schedule, leaving the rest of
# the page alone — which is what makes this affordable.
#
# The cost argument is the whole design. A full refresh re-paginates the match
# list: at a 1000-game target that's 10 calls for ids plus 4 for account,
# league, summoner and mastery — 14 calls, *even when nothing has changed*.
# Each poll below is one call. It escalates to a real refresh only when the
# newest ids differ from what's loaded.
#
# At five minutes that's ~2 requests per 5 minutes against a dev-key budget of
# 100 per 2 minutes: under 1% of the allowance.
POLL_SECONDS = 300


@st.fragment(run_every=POLL_SECONDS if st.session_state.get("auto_refresh") else None)
def poll_for_new_games():
    """Cheap check for finished games; full reload only if there are any."""
    if not st.session_state.get("auto_refresh"):
        return
    latest = client.latest_match_ids(puuid, count=20)
    if latest is None:
        # Almost always an expired dev key — they last 24 hours. Say so
        # rather than silently going stale, but don't interrupt the page.
        st.session_state.poll_status = "auto-refresh paused — check your API key"
        return
    new_ids = unseen_match_ids(latest, st.session_state.get("loaded_match_ids"))
    if not new_ids:
        st.session_state.poll_status = f"checked {relative_time(compat.utcnow())}"
        return
    st.session_state.poll_status = f"found {len(new_ids)} new game(s)"
    # A full rerun, not just this fragment: new games change every page.
    st.rerun(scope="app")


@st.fragment(run_every=POLL_SECONDS if st.session_state.get("auto_refresh") else None)
def poll_live_game():
    """Notice on your own that you're in a game, instead of on button press."""
    if not st.session_state.get("auto_refresh"):
        return
    st.session_state.live_game = client.get_active_game(puuid)
    st.session_state.live_game_checked = True


if st.session_state.get("auto_refresh"):
    poll_for_new_games()
    poll_live_game()


# ==================== Navigation ====================
PAGES = [
    st.Page(views.page_home, title="Home", icon="🏠", default=True),
    st.Page(views.page_champions, title="Champions", icon="🧙"),
    st.Page(views.page_trends, title="Trends", icon="📈"),
    st.Page(views.page_deepdive, title="Deep-Dive", icon="🔍"),
    st.Page(views.page_compare, title="Compare", icon="⚔️"),
    st.Page(views.page_roles, title="Roles", icon="🛡️"),
    st.Page(views.page_duo, title="Teammates", icon="🤝"),
    st.Page(views.page_tilt, title="Tilt", icon="🔥"),
    st.Page(views.page_other_modes, title="Other Modes", icon="🎲"),
    st.Page(views.page_raw, title="Raw Data", icon="🗂️"),
]

# Tabs across the top rather than a sidebar. `position="top"` needs
# Streamlit 1.46+ (requirements.txt enforces that), but an older install
# would raise TypeError on the unknown keyword rather than degrading — so
# fall back to the default sidebar nav instead of refusing to start.
try:
    pg = st.navigation(PAGES, position="top")
except TypeError:
    pg = st.navigation(PAGES)

# Bind and render as one atomic step — see runtime.py. Everything slow (the
# Riot fetch) has already happened above, outside the lock.
with runtime.render_lock:
    runtime.bind(**{**BASE_BINDING, **PAGE_BINDING})
    pg.run()
