"""
Shared render helpers — the reusable chunks of UI that appear on
more than one page.

Runtime state (the loaded DataFrames, the resolved accent colours, the Riot
client and so on) is injected into this module's namespace by `app.py` on
every rerun — see `runtime.bind()`. That's deliberate: Streamlit re-executes
the whole script each interaction, so the alternative was threading two dozen
values through every function signature. The tradeoff is that these names
look undefined to a linter reading this file alone; `tools/check_bindings.py`
verifies at build time that every one of them is actually provided.
"""
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import ddragon
import insights
import live_tips
import rank_history
import recap
import stats
from themes import hex_to_rgb
from stats import *  # noqa: F403 - the stats layer is this app's vocabulary


# ==================== Shared helpers ====================
def champion_card_grid(champ_wr_df: pd.DataFrame, max_cards: int = 8, roles: dict | None = None):
    top = champ_wr_df.head(max_cards)
    cols = st.columns(4)
    for i, (_, row) in enumerate(top.iterrows()):
        col = cols[i % 4]
        icon = ddragon.champion_icon_url(row["champion"], version)
        wr_class = "wr-good" if row["win_rate"] >= 50 else "wr-bad"
        # Modal role for this champion, when known — a champion played in
        # two positions still gets one badge, which is why the Deep-Dive has
        # a full per-role split rather than relying on this alone.
        role = (roles or {}).get(row["champion"])
        role_html = f'<span class="role-badge">{role}</span><br/>' if role else ""
        col.markdown(
            f"""<div class="champ-card">
                <img src="{icon}" width="64"/><br/>
                <span class="champ-name">{row['champion']}</span><br/>
                {role_html}
                <span class="{wr_class}">{row['win_rate']}%</span> ({row['games']}g)
                </div>""",
            unsafe_allow_html=True,
        )


def pretty_trend_chart(data: pd.DataFrame, column: str, title: str, window: int = 10):
    trend = rolling_trend(data, column, window=window)
    fig = go.Figure()
    wins = trend[trend["win"]]
    losses = trend[~trend["win"]]
    fig.add_trace(go.Scatter(
        x=wins["game_creation"], y=wins[column], mode="markers", name="Win",
        marker=dict(color="#2DD4BF", size=8),
    ))
    fig.add_trace(go.Scatter(
        x=losses["game_creation"], y=losses[column], mode="markers", name="Loss",
        marker=dict(color="#FB7185", size=8),
    ))
    fig.add_trace(go.Scatter(
        x=trend["game_creation"], y=trend[f"{column}_rolling"], mode="lines",
        name=f"{window}-game avg", line=dict(color=accent, width=3),
    ))
    fig.update_layout(
        template=PLOT_TEMPLATE, title=title,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=60),
    )
    return fig


def sparkline(y_values, color: str, label: str):
    fig = go.Figure(go.Scatter(
        y=list(y_values), mode="lines", line=dict(color=color, width=2.5),
        fill="tozeroy", fillcolor=f"rgba({hex_to_rgb(color)},0.15)",
    ))
    fig.update_layout(
        template=PLOT_TEMPLATE, height=90, margin=dict(l=0, r=0, t=24, b=0),
        showlegend=False, title=dict(text=label, font=dict(size=12)),
    )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


def render_sparkline_strip(data: pd.DataFrame):
    # Stacked vertically rather than in st.columns(3) — this now lives in a
    # narrower card next to the Recommendations feature card,
    # where three side-by-side mini-charts would be too cramped to read.
    trend = data.sort_values("game_creation").copy()
    if len(trend) >= 2:
        trend["win_pct"] = trend["win"].astype(int).rolling(10, min_periods=1).mean() * 100
        st.plotly_chart(
            sparkline(trend["win_pct"], accent, "Win % (10-game roll)"),
            use_container_width=True, config={"displayModeBar": False},
        )
        trend["kda_roll"] = trend["kda"].rolling(10, min_periods=1).mean()
        st.plotly_chart(
            sparkline(trend["kda_roll"], "#2DD4BF", "KDA (10-game roll)"),
            use_container_width=True, config={"displayModeBar": False},
        )
    else:
        st.caption("Not enough games yet for a win % sparkline.")
        st.caption("Not enough games yet for a KDA sparkline.")
    if len(lp_hist) >= 2:
        st.plotly_chart(
            sparkline(lp_hist["league_points"], "#A855F7", "LP (tracked history)"),
            use_container_width=True, config={"displayModeBar": False},
        )
    else:
        st.caption("LP sparkline will build up the more you use this dashboard.")


def render_scoreboard_side(container, side_df: pd.DataFrame):
    for _, p in side_df.iterrows():
        icon = ddragon.champion_icon_url(p["champion"], version)
        items_html = "".join(
            f'<img class="item-icon" src="{ddragon.item_icon_url(i, version)}" width="18"/>'
            for i in p["items"] if i
        )
        row_class = "scoreboard-row me" if p["is_me"] else "scoreboard-row"
        container.markdown(
            f"""<div class="{row_class}">
                <img src="{icon}" width="26" style="border-radius:5px;"/>
                <span class="name">{p['champion']}</span>
                <span class="cs">{p.get('role_label', '')}</span>
                <span class="kda">{p['kills']}/{p['deaths']}/{p['assists']}</span>
                <span class="cs">{p['cs']} cs</span>
                <div>{items_html}</div>
                </div>""",
            unsafe_allow_html=True,
        )


def recent_games_feed(data: pd.DataFrame, n: int = 20):
    recent = data.sort_values("game_creation", ascending=False).head(n)
    for _, row in recent.iterrows():
        icon = ddragon.champion_icon_url(row["champion"], version)
        result_color = "#2DD4BF" if row["win"] else "#FB7185"
        result_text = "WIN" if row["win"] else "LOSS"
        items_html = "".join(
            f'<img class="item-icon" src="{ddragon.item_icon_url(i, version)}" width="22"/>'
            for i in row["items"] if i
        )
        date_str = row["game_creation"].strftime("%b %d, %I:%M %p")
        st.markdown(
            f"""<div class="game-row">
                <img class="champ-icon" src="{icon}" width="48"/>
                <div class="col" style="min-width:170px;">
                    <b>{row['champion']}</b><br/>
                    <span style="color:{result_color};font-weight:700;">{result_text}</span>
                    · {row.get('role_label', 'Unknown')} · {row['queue_type']} · {date_str}
                </div>
                <div class="col" style="min-width:120px;">
                    <b>{row['kills']}/{row['deaths']}/{row['assists']}</b><br/>{row['kda']} KDA
                </div>
                <div class="col" style="min-width:140px;">
                    {row['cs_per_min']} CS/min<br/>{row['vision_score']} vision
                </div>
                <div class="col" style="min-width:90px;">Patch {row.get('patch', '?')}</div>
                <div class="col" style="min-width:70px;">{row['game_duration_min']} min</div>
                <div>{items_html}</div>
                </div>""",
            unsafe_allow_html=True,
        )
        with st.expander("Match details"):
            match = client.get_match(row["match_id"], use_cache=True)
            board = match_scoreboard(match, puuid)
            col_a, col_b = st.columns(2)
            col_a.markdown("**Your Team**")
            render_scoreboard_side(col_a, board[board["side"] == "Your Team"])
            col_b.markdown("**Enemy Team**")
            render_scoreboard_side(col_b, board[board["side"] == "Enemy Team"])


# How many recent ranked games each tip window covers. "All" keeps the old
# behaviour; the smaller windows exist because a pattern computed over 300
# games barely moves when you play ten more, which made the whole card look
# frozen even though it recomputes on every rerun.
TIP_WINDOWS = {
    "Last 50": 50,
    "Last 100": 100,
    "Last 250": 250,
    "All games": None,
}


def render_recommendations(data: pd.DataFrame, window: int | None = None):
    """Pattern tips, each annotated with the sample it rests on.

    `window` trims to the most recent N games before computing anything, so
    the patterns track current form rather than career averages.
    """
    tone_icon = {"positive": "🟢", "warning": "🟡", "neutral": "🔵"}
    data = recent_window(data, window)

    tips = insights.generate_recommendations(data)

    # An all-grey card looks broken. It isn't — it's the honest result for
    # most samples this size, and saying so is more useful than letting the
    # user guess whether the feature failed.
    real = [t for t in tips if not t.get("weak")]
    if tips and not real:
        st.caption(
            "Nothing here clears the noise threshold on this many games. "
            "That's a real finding, not a glitch: day-of-week and time-of-day "
            "splits need a lot of games before they mean anything."
        )
    for tip in tips:
        icon = tone_icon.get(tip["tone"], "•")
        games, margin = tip.get("games", 0), tip.get("margin", 0.0)
        # The margin is the honest part: +/-15 points on 38 games is the
        # difference between a finding and a coin flip.
        detail = f"{games} games · ±{margin:.0f}pp" if games else ""
        if tip.get("weak"):
            detail += " · within noise"
        css = "tip-row tip-weak" if tip.get("weak") else "tip-row"
        st.markdown(
            f"""<div class="{css}"><span class="icon">{icon}</span>
                <span class="text">{tip['text']}
                <span class="tip-meta">{detail}</span></span></div>""",
            unsafe_allow_html=True,
        )


def render_patch_win_rate(data: pd.DataFrame, title: str):
    patch_wr = win_rate_by_patch(data, min_games=3)
    if patch_wr.empty:
        st.caption("Not enough games on any single patch yet (need 3+ per patch).")
        return
    fig = px.bar(
        patch_wr, x="patch", y="win_rate", hover_data=["games", "wins"],
        labels={"win_rate": "Win rate (%)", "patch": "Patch"}, title=title,
    )
    fig.update_layout(template=PLOT_TEMPLATE)
    st.plotly_chart(fig, use_container_width=True)


def render_keystone_win_rates(data: pd.DataFrame):
    runes_catalog = get_runes_catalog(version)
    wr = win_rate_by(data, "keystone_id", min_games=3)
    if wr.empty:
        st.caption("Not enough games on any single keystone yet (need 3+).")
        return
    cols = st.columns(4)
    for i, (_, row) in enumerate(wr.head(8).iterrows()):
        perk = runes_catalog["perks"].get(int(row["keystone_id"]), {})
        name = perk.get("name", f"Rune {int(row['keystone_id'])}")
        col = cols[i % 4]
        if perk.get("icon"):
            col.image(ddragon.rune_icon_url(perk["icon"]), width=44)
        wr_color = "#2DD4BF" if row["win_rate"] >= 50 else "#FB7185"
        col.markdown(
            f"**{name}**  \n<span style='color:{wr_color};font-weight:700;'>{row['win_rate']}%</span> ({int(row['games'])}g)",
            unsafe_allow_html=True,
        )


def render_summoner_combo_win_rates(data: pd.DataFrame):
    spells_catalog = get_summoner_spells_catalog(version)
    combos = data[data["summoner_combo"].map(len) == 2]
    wr = win_rate_by(combos, "summoner_combo", min_games=3)
    if wr.empty:
        st.caption("Not enough games on any single summoner spell combo yet (need 3+).")
        return
    for _, row in wr.head(8).iterrows():
        s1, s2 = row["summoner_combo"]
        spell1 = spells_catalog.get(int(s1), {})
        spell2 = spells_catalog.get(int(s2), {})
        icons_html = "".join(
            f'<img class="item-icon" src="{s.get("icon", "")}" width="28"/>'
            for s in (spell1, spell2) if s.get("icon")
        )
        names = f"{spell1.get('name', s1)} + {spell2.get('name', s2)}"
        wr_color = "#2DD4BF" if row["win_rate"] >= 50 else "#FB7185"
        st.markdown(
            f"""<div class="build-row">
                {icons_html}
                <span>{names}</span>
                <span style="color:{wr_color};font-weight:700;">{row['win_rate']}%</span>
                <span>({int(row['games'])}g)</span>
                </div>""",
            unsafe_allow_html=True,
        )


def render_performance_radar(data: pd.DataFrame, objectives_df=None, label: str = "You"):
    """Eight-dimension 'fingerprint' radar. Scored against fixed reference
    bands rather than rank-matched peers (see performance_radar's docstring)
    — the caption says so, because presenting a self-relative score as a
    peer comparison would be misleading."""
    scores = performance_radar(data, objectives_df)
    rift = rift_only(data)
    excluded = len(data) - len(rift)
    if all(v == 0 for v in scores.values()):
        # Distinguish "nothing loaded" from "loaded, but none of it is
        # Summoner's Rift" — the second isn't a lack of data, it's a
        # mismatch between the data and what this chart can describe.
        if not data.empty and rift.empty:
            st.caption(
                "No Summoner's Rift games in this filter. The profile is scored against "
                "Rift reference bands, and ARAM/Arena don't map onto them — deaths, "
                "assists and vision all behave differently there."
            )
        else:
            st.caption("Not enough data yet for a performance profile.")
        return

    dims = list(scores.keys())
    values = [scores[d] for d in dims]
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=values + [values[0]],           # close the loop back to the start
        theta=dims + [dims[0]],
        fill="toself",
        name=label,
        line=dict(color=accent, width=2.5),
        fillcolor=f"rgba({accent_rgb},0.25)",
    ))
    fig.update_layout(
        template=PLOT_TEMPLATE,
        polar=dict(
            bgcolor="rgba(0,0,0,0)",
            radialaxis=dict(visible=True, range=[0, 100], gridcolor=GRID_COLOR, tickfont=dict(size=10)),
            angularaxis=dict(gridcolor=GRID_COLOR),
        ),
        showlegend=False,
        height=420,
        margin=dict(l=60, r=60, t=40, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

    strongest, weakest = radar_highlights(scores)
    if strongest and weakest and strongest != weakest:
        st.markdown(
            f"Your profile leans toward **{strongest}** "
            f"(<span style='color:{accent_text};font-weight:700;'>{scores[strongest]}</span>), "
            f"with **{weakest}** lowest "
            f"(<span style='color:{accent_text};font-weight:700;'>{scores[weakest]}</span>).",
            unsafe_allow_html=True,
        )
    excluded_note = (
        f" {excluded} ARAM/Arena game(s) excluded — those modes distort nearly every "
        "dimension, so scoring them against Rift bands would measure how much ARAM you "
        "play rather than how you play."
        if excluded else ""
    )
    st.caption(
        f"Based on {len(rift)} Summoner's Rift game(s).{excluded_note} "
        "Scored 0-100 against fixed reference bands — **not** against other players at "
        "your rank, which a single account's data can't support. Read it as the shape of "
        "your playstyle, not a grade. Farming is scored against your role's own CS range, "
        "so it's comparable whether you jungle, support or lane."
    )


def render_primary_focus(data: pd.DataFrame, objectives_df=None):
    """One focus area instead of a wall of equally-weighted tips. Returns
    True when it rendered something, so the caller can decide how to present
    the rest."""
    scores = performance_radar(data, objectives_df)
    focus = primary_focus(scores, data)
    if not focus:
        st.caption(
            "No single standout weak point right now — your profile is fairly even, "
            "and picking one out of a flat spread would be reading noise."
        )
        return False

    st.markdown(
        f"""<div class="tip-row" style="border-left-width:5px;">
            <span class="icon">🎯</span>
            <span class="text">
                <b style="font-size:1.05rem;">{focus['dimension']}</b>
                &nbsp;<span style="color:{accent_text};font-weight:700;">{focus['score']}/100</span><br/>
                {focus['observation']}<br/>
                <b>Try this:</b> {focus['suggestion']}
            </span></div>""",
        unsafe_allow_html=True,
    )
    if focus.get("caveat"):
        st.caption(focus["caveat"])
    st.caption(
        f"Lowest of your eight profile dimensions across {focus['games']} games in this filter. "
        "A suggestion to pay attention to, not a diagnosis — no stats page can see *why* you're "
        "losing games."
    )
    return True


def render_win_probability_review(data: pd.DataFrame):
    """Coachless-style game review: pick a game, see how win probability
    moved, and get the biggest swings as timestamps to actually go watch."""
    table = st.session_state.wp_table
    series_by_match = st.session_state.wp_series
    if not table or not series_by_match:
        st.caption("Load deep match analytics above first to build this.")
        return

    total_samples = sum(e["games"] for e in table.values())
    scoped = data[data["match_id"].isin(series_by_match.keys())]
    if scoped.empty:
        st.caption("No games in the current queue filter have timeline data loaded yet.")
        return

    options = {
        f"{row['game_creation'].strftime('%b %d, %I:%M %p')} — {row['champion']} "
        f"({'Win' if row['win'] else 'Loss'})": row["match_id"]
        for _, row in scoped.sort_values("game_creation", ascending=False).iterrows()
    }
    picked_label = st.selectbox("Review a game", list(options.keys()), key="wp_game")
    picked_id = options[picked_label]

    curve = win_probability_curve(series_by_match.get(picked_id, []), table)
    if curve.empty:
        st.caption("No timeline data for this game.")
        return

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=curve["minute"], y=curve["win_prob"], mode="lines",
        line=dict(color=accent, width=3), name="Win probability",
        hovertemplate="Min %{x}<br>%{y:.0f}%<extra></extra>",
    ))
    fig.add_hline(y=50, line_dash="dot", line_color=GRID_COLOR)
    fig.update_layout(
        template=PLOT_TEMPLATE, yaxis_title="Win probability (%)",
        xaxis_title="Minute", yaxis=dict(range=[0, 100]), height=340,
    )
    st.plotly_chart(fig, use_container_width=True)

    swings = biggest_swings(curve)
    if swings:
        st.markdown("**Biggest swings — the moments worth rewatching**")
        for s in swings:
            color = "#2DD4BF" if s["direction"] == "gain" else "#FB7185"
            sign = "+" if s["delta"] > 0 else ""
            st.markdown(
                f"""<div class="game-row">
                    <div class="col" style="min-width:120px;">
                        <b>{s['start_minute']}–{s['end_minute']} min</b>
                    </div>
                    <div class="col">
                        <span style="color:{color};font-weight:700;font-size:1.05rem;">
                        {sign}{s['delta']}%</span> win probability
                    </div>
                    </div>""",
                unsafe_allow_html=True,
            )
    else:
        st.caption("No clear swings found — either a steady game, or not enough history to judge.")

    st.caption(
        f"Win probability here is **empirical, from your own games** — it reads "
        f"'when my team was this far ahead at this point, I went on to win X of Y times', "
        f"built from {len(series_by_match)} games ({total_samples:,} state samples). "
        "It is not a trained model over millions of matches like Coachless or similar sites use, "
        "so treat it as a rough guide to where a game shifted, not a precise probability. "
        "Points with too few comparable games fall back to 50%."
    )


def render_live_game():
    """Current game via spectator-v5, if you're in one.

    Fetched on demand rather than on every page load — it's one more API
    call against a rate-limited dev key, and the answer is "no" most of the
    time. Not being in a game is the normal case, so that state is phrased
    plainly rather than as an error."""
    if st.button("Check for a live game"):
        with st.spinner("Asking Riot..."):
            st.session_state.live_game = client.get_active_game(puuid)
            st.session_state.live_game_checked = True

    if not st.session_state.get("live_game_checked"):
        st.caption("Checks whether you're in a game right now, and shows both lobbies.")
        return

    game = st.session_state.get("live_game")
    if not game:
        st.info("Not in a game right now.")
        return

    champs = get_champions_catalog(version)
    length_min = max(int(game.get("gameLength", 0) / 60), 0)
    st.markdown(
        f"**{queue_label(game.get('gameQueueConfigId'))}** · "
        f"in progress {length_min} min"
    )

    participants = game.get("participants", [])
    me = next((p for p in participants if p.get("puuid") == puuid), None)
    my_team = me.get("teamId") if me else 100

    col_mine, col_theirs = st.columns(2)
    for column, team_id, label in (
        (col_mine, my_team, "Your Team"),
        (col_theirs, 200 if my_team == 100 else 100, "Enemy Team"),
    ):
        with column:
            st.markdown(f"**{label}**")
            for p in [x for x in participants if x.get("teamId") == team_id]:
                info = champs.get(p.get("championId"), {})
                name = info.get("id", "")
                icon = ddragon.champion_icon_url(name, version) if name else ""
                is_me = p.get("puuid") == puuid
                row_class = "scoreboard-row me" if is_me else "scoreboard-row"
                display = p.get("riotId") or p.get("summonerName") or "?"
                st.markdown(
                    f"""<div class="{row_class}">
                        <img src="{icon}" width="26" style="border-radius:5px;"/>
                        <span class="name">{info.get('name', 'Unknown')}</span>
                        <span class="cs">{display}</span>
                        </div>""",
                    unsafe_allow_html=True,
                )

    bans = [b for b in game.get("bannedChampions", []) if b.get("championId", -1) > 0]
    if bans:
        ban_html = "".join(
            f'<img class="item-icon" width="26" '
            f'src="{ddragon.champion_icon_url(champs[b["championId"]]["id"], version)}"/>'
            for b in bans if b.get("championId") in champs
        )
        st.markdown(f"**Bans** <div class=\"build-row\">{ban_html}</div>", unsafe_allow_html=True)

    st.divider()
    render_live_tips(game, participants, me, my_team, champs)


def _champion_name(participant, champs):
    return champs.get(participant.get("championId"), {}).get("id", "")


def render_live_tips(game, participants, me, my_team, champs):
    """Advice for the game in progress, from your own history.

    Everything here is a fact about games you've played, not a model of the
    champions involved. Roles are inferred — spectator-v5 has no position
    field — so the inference is labelled and overridable rather than
    presented as truth.
    """
    if not me:
        st.caption("Couldn't find you in this lobby, so no tips.")
        return

    queue_id = game.get("gameQueueConfigId")
    if not is_core_queue(queue_id):
        st.info(
            f"This is {mode_label(queue_id, game.get('gameMode'))}, which sits "
            "outside the history these tips are built from. Nothing useful to say."
        )
        return

    history = df  # already core-only; see the split in app.py
    if history.empty:
        st.caption("No match history loaded yet.")
        return

    my_champ = _champion_name(me, champs)
    enemy_team_id = 200 if my_team == 100 else 100
    allies = [p for p in participants if p.get("teamId") == my_team and p is not me]
    enemies = [p for p in participants if p.get("teamId") == enemy_team_id]

    priors = live_tips.champion_role_priors(history)

    def as_team(players):
        return [{
            "puuid": p.get("puuid"),
            "champion": _champion_name(p, champs),
            "spells": (p.get("spell1Id"), p.get("spell2Id")),
        } for p in players]

    my_side = live_tips.infer_roles(as_team([me] + allies), priors)
    their_side = live_tips.infer_roles(as_team(enemies), priors)

    inferred_role, confidence = my_side.get(me.get("puuid"), (None, "guess"))

    # Let the guess be corrected. Roles drive which slice of history the tips
    # use, so a wrong one doesn't just mislabel — it answers a different
    # question entirely.
    # LANE_ORDER, not ROLE_ORDER — the latter includes ARAM/Arena/Unknown.
    role_options = ["Auto"] + list(live_tips.LANE_ORDER)
    picked = st.selectbox(
        f"Your role — inferred **{inferred_role or 'unknown'}** ({confidence})",
        role_options, index=0, key="live-role",
        help="Riot's live-game endpoint doesn't include positions, so this is "
             "worked out from Smite and from which roles your history "
             "associates with each champion. Override it if it's wrong.",
    )
    my_role = inferred_role if picked == "Auto" else picked

    # The lane opponent is whoever the enemy inference put in the same role.
    opponent = next(
        (_champion_name(p, champs) for p in enemies
         if their_side.get(p.get("puuid"), (None,))[0] == my_role),
        None,
    )
    opp_conf = next(
        (their_side.get(p.get("puuid"), (None, "guess"))[1] for p in enemies
         if their_side.get(p.get("puuid"), (None,))[0] == my_role),
        "guess",
    )

    tips = live_tips.build_live_tips(
        history, my_champ, my_role, opponent,
        tuple(_champion_name(p, champs) for p in allies),
        tuple(_champion_name(p, champs) for p in enemies),
    )

    header = f"**{my_champ} · {my_role or 'unknown role'}**"
    if opponent:
        header += f" vs **{opponent}**"
        if opp_conf != "certain":
            header += f" _({opp_conf} lane opponent)_"
    st.markdown(header)

    if not tips:
        st.caption("Nothing in your history to say about this one yet.")
        return

    tone_icon = {"positive": "🟢", "warning": "🟡", "neutral": "🔵"}
    for tip in tips:
        detail = f"{tip['games']} games · ±{tip['margin']:.0f}pp" if tip["games"] else ""
        if tip.get("weak"):
            detail += " · within noise"
        if tip.get("note"):
            detail += f" · {tip['note']}"
        css = "tip-row tip-weak" if tip.get("weak") else "tip-row"
        st.markdown(
            f"""<div class="{css}"><span class="icon">{tone_icon.get(tip['tone'], '•')}</span>
                <span class="text">{tip['text']}
                <span class="tip-meta">{detail}</span></span></div>""",
            unsafe_allow_html=True,
        )

    st.caption(
        "All of this is your own results, not a read on the champions. It "
        "describes what has happened, not what will — and it is not a reason "
        "to dodge."
    )


def render_position_heatmap(pos_df: pd.DataFrame, title: str):
    if pos_df is None or pos_df.empty:
        st.caption(
            "No position data loaded yet — load deep match analytics above first. "
            "(Note: this covers deaths and kills only — Riot's public API doesn't "
            "expose ward placement locations for any player, for privacy/competitive "
            "reasons, so a ward heatmap isn't possible from this data.)"
        )
        return
    fig = go.Figure()
    fig.add_layout_image(dict(
        source=ddragon.map_image_url(version),
        xref="x", yref="y", x=0, y=MAP_SIZE,
        sizex=MAP_SIZE, sizey=MAP_SIZE,
        xanchor="left", yanchor="top",
        sizing="stretch", layer="below", opacity=0.85,
    ))
    deaths = pos_df[pos_df["kind"] == "death"]
    kills = pos_df[pos_df["kind"] == "kill"]
    fig.add_trace(go.Scatter(
        x=deaths["x"], y=deaths["y"], mode="markers", name="Deaths",
        marker=dict(color="#FB7185", size=9, opacity=0.6, line=dict(width=1, color="#1A0A0D")),
    ))
    fig.add_trace(go.Scatter(
        x=kills["x"], y=kills["y"], mode="markers", name="Kills",
        marker=dict(color="#2DD4BF", size=9, opacity=0.6, line=dict(width=1, color="#0A1A17")),
    ))
    fig.update_xaxes(visible=False, range=[0, MAP_SIZE])
    fig.update_yaxes(visible=False, range=[0, MAP_SIZE], scaleanchor="x", scaleratio=1)
    fig.update_layout(
        template=PLOT_TEMPLATE, title=title, height=560,
        margin=dict(l=0, r=0, t=50, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_highlight_reel(data: pd.DataFrame):
    highlights = standout_games(data)
    if not highlights:
        st.caption("Not enough games yet for a highlight reel.")
        return
    for h in highlights:
        row = h["row"]
        icon = ddragon.champion_icon_url(row["champion"], version)
        badge_color = "#2DD4BF" if h["tone"] == "positive" else "#FB7185"
        date_str = row["game_creation"].strftime("%b %d")
        st.markdown(
            f"""<div class="game-row">
                <img class="champ-icon" src="{icon}" width="48"/>
                <div class="col" style="min-width:210px;">
                    <span style="color:{badge_color};font-weight:700;">{h['label']}</span><br/>
                    <b>{row['champion']}</b> · {row.get('role_label', 'Unknown')} · {date_str}
                </div>
                <div class="col" style="min-width:120px;">
                    <b>{row['kills']}/{row['deaths']}/{row['assists']}</b><br/>{row['kda']} KDA
                </div>
                <div class="col" style="min-width:110px;">{row['damage_share']}% dmg share</div>
                </div>""",
            unsafe_allow_html=True,
        )


# The replay finder lived here. It matched standout games against `.rofl`
# files on the machine running the app — fine for a local tool, meaningless
# hosted: the container's disk has no replays, and any it did have wouldn't
# belong to whoever is looking at the page. Riot also can't serve replays
# remotely, and keeps a game replayable for only about two weeks, so there is
# no hosted version of this feature to build.


# ---------------------------------------------------------------------------
# Public surface, frozen at import time.
#
# app.py splats these into runtime.bind() so every page can call them without
# importing. It used to compute the list with `dir(components)` at bind time,
# which broke as soon as Streamlit reran the script: bind() writes names *into*
# this module, so on the next rerun `dir()` also returned the injected ones —
# including `render_hero`, which actually lives in layout.py and was already
# being passed explicitly. Hence "bind() got multiple values for keyword
# argument 'render_hero'" on the first click, but never on first load.
#
# Computing it here fixes that at the root: this line runs once, on import,
# before anything has been injected, so the tuple can only ever contain names
# this module genuinely defines.
# ---------------------------------------------------------------------------
_EXPORT_PREFIXES = ("render_", "recent_games_feed", "champion_card_grid",
                    "pretty_trend_chart", "sparkline")
EXPORTS = tuple(sorted(
    name for name, obj in list(globals().items())
    if name.startswith(_EXPORT_PREFIXES) and callable(obj)
))

