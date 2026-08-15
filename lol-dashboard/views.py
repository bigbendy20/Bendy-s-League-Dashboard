"""
Page bodies. One function per tab; `app.py` wires them into st.navigation.

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

import ddragon
import insights
import rank_history
import recap
import replays
import stats
# A plain constant, so it comes in as a normal import rather than through
# the runtime binding — that only carries callables.
from components import TIP_WINDOWS
from stats import *  # noqa: F403 - the stats layer is this app's vocabulary


# ==================== Page: Home ====================
def page_home():
    games, wins_n, wr = overall_win_rate(filtered_df)
    avgs = averages(filtered_df)
    # The headline number is your *official* Ranked Solo/Duo record from
    # Riot — the whole season, not just the matches this app has pulled, and
    # not diluted by ARAM/normals the way the queue-filtered figure is. The
    # filtered win rate still lives in the Overview card, which is explicitly
    # labelled with the queue filter so the two can't be confused.
    if solo:
        solo_wins, solo_losses = solo.get("wins", 0), solo.get("losses", 0)
        solo_total = solo_wins + solo_losses
        solo_wr = round(solo_wins / max(solo_total, 1) * 100, 1)
        hero_stats = [
            ("Ranked Win Rate", f"{solo_wr}%"),
            ("Ranked W/L", f"{solo_wins}W {solo_losses}L"),
            ("LP", solo["leaguePoints"]),
        ]
        hero_subtitle = f"Ranked Solo/Duo this season · viewing {queue_filter}"
    else:
        # Unranked or off-season: fall back to the loaded history, labelled
        # honestly rather than presenting it as a ranked record.
        hero_stats = [("Games", games), (f"Win Rate ({queue_filter})", f"{wr}%")]
        hero_subtitle = f"No ranked Solo/Duo data yet · viewing {queue_filter}"
    render_hero(APP_TITLE, hero_subtitle, stats=hero_stats)

    # Active streak callout — distinct from the retrospective "win rate after
    # a loss" stat further down. Only surfaced at 3+, since a 1-2 game run is
    # noise, and a loss streak is deliberately framed as neutral information
    # rather than something alarming.
    streak_kind, streak_len = current_streak(filtered_df)
    if streak_len >= 3:
        if streak_kind == "win":
            st.success(f"🔥 You're on a **{streak_len}-game win streak** right now.")
        else:
            st.warning(
                f"You're on a **{streak_len}-game loss streak** right now — "
                "worth a break if you're feeling it."
            )

    with section_card(
        "home-live", "Live Game", "Are you in a game right now?", icon="🔴",
    ):
        render_live_game()

    col_l, col_r = st.columns([2, 1])
    with col_l:
        with section_card(
            "home-recs", "Work On This",
            "One thing at a time beats a list you'll skim. Everything else is tucked below.",
            icon="🎯", featured=True,
        ):
            if ranked_df.empty:
                st.caption("No ranked games loaded yet — this needs Ranked Solo/Duo or Flex data.")
            else:
                render_primary_focus(
                    ranked_df, st.session_state.objectives_cache.get("__general__")
                )
                with st.expander("Other patterns worth knowing"):
                    st.caption(
                        "Rule-of-thumb observations from your Ranked Solo/Duo + Flex games only "
                        "(regardless of the queue filter above) — patterns, not coaching advice. "
                        "Times and days are your local time."
                    )
                    window_label = st.selectbox(
                        "Window", list(TIP_WINDOWS.keys()), index=1,
                        key="tips_window",
                        help=(
                            "Patterns over your whole history barely move when you play a few "
                            "more games. A shorter window tracks current form — at the cost of "
                            "a wider margin of error."
                        ),
                    )
                    render_recommendations(ranked_df, TIP_WINDOWS[window_label])
                    st.caption(
                        "± is the 95% confidence margin. Days and hours are picked by scanning "
                        "every bucket and reporting the extreme, so the winner is flattered by "
                        "chance — treat anything marked *within noise* as a curiosity."
                    )
    with col_r:
        with section_card("home-snapshot", "Snapshot", icon="📊"):
            render_sparkline_strip(filtered_df)

    with section_card(
        "home-radar", "Player Profile",
        "Eight dimensions of how you play, as one shape.",
        icon="🕸️", featured=True,
    ):
        render_performance_radar(
            filtered_df, st.session_state.objectives_cache.get("__general__")
        )

    with section_card(
        "home-highlights", "Highlight Reel",
        "Auto-flagged standout games from your current queue filter — best and worst, a few different lenses.",
        icon="🌟", featured=True,
    ):
        render_highlight_reel(filtered_df)
        st.markdown("**Find replays for these games**")
        render_replay_finder(filtered_df)

    col_l2, col_r2 = st.columns(2)
    with col_l2:
        with section_card("home-ranked", "Ranked Standing", icon="🏆"):
            if solo:
                wins, losses = solo.get("wins", 0), solo.get("losses", 0)
                ranked_wr = round(wins / max(wins + losses, 1) * 100, 1)
                metric_grid([
                    ("Tier", f"{solo['tier'].title()} {solo['rank']}"),
                    ("LP", solo["leaguePoints"]),
                    ("Ranked W/L", f"{wins}W {losses}L"),
                    ("Ranked Win Rate", f"{ranked_wr}%"),
                ], cols_per_row=2)
            else:
                st.caption("No ranked solo queue data found for this account (unranked, or off-season).")

            since = rank_history.lp_since_last_check(lp_hist)
            if since:
                delta = since["delta"]
                if since["promoted"]:
                    st.success(
                        f"You've moved to **{since['tier'].title()} {since['rank']}** "
                        f"since the previous refresh ({delta:+} LP equivalent)."
                    )
                elif delta:
                    st.markdown(
                        f"**{delta:+} LP** since the previous refresh "
                        f"({relative_time(since['since'])})."
                    )

            if len(lp_hist) >= 2:
                fig = px.line(
                    lp_hist, x="timestamp", y="league_points", markers=True,
                    title="LP Over Time (tracked since you started using this dashboard)",
                )
                fig.update_layout(template=PLOT_TEMPLATE)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.caption(
                    "LP trend will build up the more you use this dashboard — Riot's API only "
                    "exposes your *current* LP, not history, so there's no way to backfill the past."
                )
    with col_r2:
        with section_card("home-overview", f"Overview — {queue_filter}", icon="📈"):
            metric_grid([
                ("Games", games),
                ("Win Rate", f"{wr}%"),
                ("Avg KDA", avgs["kda"]),
                ("Avg CS/min", avgs["cs_per_min"]),
                ("Avg Vision", avgs["vision_score"]),
                ("Avg Kill Participation", f"{avgs['kill_participation']}%"),
                ("Avg Damage Share", f"{avgs['damage_share']}%"),
                ("Avg CC Score", avgs["cc_score"]),
            ], cols_per_row=2)

    col_l25, col_r25 = st.columns(2)
    with col_l25:
        with section_card(
            "home-goal", "Climb Goal",
            "Set a target in Settings → Climb goal.",
            icon="🎯",
        ):
            if not GOAL_TIER:
                st.caption("No goal set yet — pick one in **Settings** at the top of the page.")
            elif not solo:
                st.caption("No ranked solo data yet to measure progress from.")
            else:
                current_value = rank_history.climb_value(solo["tier"], solo["rank"], solo["leaguePoints"])
                goal_value = rank_history.climb_value(GOAL_TIER, GOAL_RANK or None, 0)
                goal_label = GOAL_TIER.title() if GOAL_TIER in rank_history.APEX_TIERS else f"{GOAL_TIER.title()} {GOAL_RANK}"
                if current_value is not None and goal_value is not None and current_value >= goal_value:
                    st.success(f"🎉 Goal reached! You're at {solo['tier'].title()} {solo['rank']}, past your {goal_label} goal.")
                else:
                    rate = rank_history.lp_gain_rate(lp_hist, window_days=7)
                    st.markdown(f"**Current:** {solo['tier'].title()} {solo['rank']} ({solo['leaguePoints']} LP) → **Goal:** {goal_label}")
                    if current_value is not None and goal_value is not None:
                        remaining = goal_value - current_value
                        if rate and rate > 0:
                            days = round(remaining / rate, 1)
                            st.metric("Est. days to goal", days, help=f"Based on your last 7 days' climb rate (~{round(rate, 1)} LP-equivalent/day).")
                        elif rate is not None:
                            st.caption("You've been losing ground over the last 7 days — no ETA to show.")
                        else:
                            st.caption("Not enough tracked history yet for a gain-rate estimate — keep using the dashboard and this fills in.")
    with col_r25:
        with section_card("home-length", "Win Rate by Game Length", "Snowballer or scaler?", icon="⏳"):
            length_wr = win_rate_by_length(filtered_df, min_games=3)
            if length_wr.empty:
                st.caption("Not enough games in any single length bucket yet (need 3+).")
            else:
                fig = px.bar(
                    length_wr, x="game_length_bucket", y="win_rate", hover_data=["games", "wins"],
                    labels={"win_rate": "Win rate (%)", "game_length_bucket": "Game length"},
                )
                fig.update_layout(template=PLOT_TEMPLATE)
                st.plotly_chart(fig, use_container_width=True)

    with section_card("home-deeper", "Deeper Numbers", icon="🔎"):
        kd_wr = kill_diff_win_rate(filtered_df)
        if not kd_wr.empty:
            fig = px.bar(
                kd_wr, x="kill_diff_bucket", y="win_rate", hover_data=["games", "wins"],
                labels={"win_rate": "Win rate (%)", "kill_diff_bucket": "Kills minus deaths"},
                title="Win Rate by Kill Differential",
            )
            fig.update_layout(template=PLOT_TEMPLATE)
            st.plotly_chart(fig, use_container_width=True)
            st.caption("How often you win when you're up kills vs. down kills (kills − deaths in that game).")

        st.markdown("**Win Rate by Patch**")
        render_patch_win_rate(filtered_df, "Win Rate by Patch (all champions)")

        st.markdown("**First-to-the-punch win rates**")
        first_stat_cols = [
            ("first_blood", "First Blood (you)"),
            ("first_tower", "First Tower (you)"),
            ("team_first_blood", "Team First Blood"),
            ("team_first_dragon", "Team First Dragon"),
            ("team_first_baron", "Team First Baron"),
            ("team_first_tower", "Team First Tower"),
        ]
        first_stat_rows = []
        for col, label in first_stat_cols:
            if col not in filtered_df.columns:
                continue
            got_it = filtered_df[filtered_df[col] == True]  # noqa: E712
            missed_it = filtered_df[filtered_df[col] == False]  # noqa: E712
            if got_it.empty and missed_it.empty:
                continue
            _, _, wr_got = overall_win_rate(got_it)
            _, _, wr_missed = overall_win_rate(missed_it)
            first_stat_rows.append({
                "Stat": label, "Games with it": len(got_it), "Win rate with": f"{wr_got}%",
                "Win rate without": f"{wr_missed}%",
            })
        if first_stat_rows:
            st.dataframe(pd.DataFrame(first_stat_rows), use_container_width=True, hide_index=True)
        else:
            st.caption("No first-blood/objective data in this set yet.")

    col_l3, col_r3 = st.columns(2)
    with col_l3:
        with section_card(
            "home-matchups", "Matchup Extremes", "Lane opponents you've faced 3+ times",
            icon="⚔️",
        ):
            nemesis, free_win = nemesis_and_free_win(filtered_df, min_games=3)
            if not nemesis and not free_win:
                st.caption("Need 3+ games against the same lane opponent to surface these yet.")
            else:
                mc1, mc2 = st.columns(2)
                if nemesis:
                    mc1.image(ddragon.champion_icon_url(nemesis["opponent_champion"], version), width=56)
                    mc1.markdown(
                        f"**Nemesis**  \n{nemesis['opponent_champion']}  \n"
                        f"<span style='color:#FB7185;font-weight:700;'>{nemesis['win_rate']}%</span> "
                        f"({int(nemesis['games'])}g)",
                        unsafe_allow_html=True,
                    )
                if free_win:
                    mc2.image(ddragon.champion_icon_url(free_win["opponent_champion"], version), width=56)
                    mc2.markdown(
                        f"**Free Win**  \n{free_win['opponent_champion']}  \n"
                        f"<span style='color:#2DD4BF;font-weight:700;'>{free_win['win_rate']}%</span> "
                        f"({int(free_win['games'])}g)",
                        unsafe_allow_html=True,
                    )
                st.caption("Same-role-opposite-team heuristic — Riot's API doesn't expose true lane assignments.")
    with col_r3:
        with section_card("home-multikills", "Multikills", icon="💥"):
            mk = multikill_summary(filtered_df)
            if sum(mk.values()) == 0:
                st.caption("No double kills or better in this set yet.")
            else:
                metric_grid(list(mk.items()), cols_per_row=2)

    with section_card(
        "home-bests", "Personal Bests", "Career highs in the current queue filter",
        icon="🏅",
    ):
        bests = personal_bests(filtered_df)
        if not bests:
            st.caption("No games loaded yet.")
        else:
            best_cols = st.columns(3)
            for i, b in enumerate(bests):
                col = best_cols[i % 3]
                result_color = "#2DD4BF" if b["win"] else "#FB7185"
                icon = ddragon.champion_icon_url(b["champion"], version) if b["champion"] else ""
                col.markdown(
                    f"""<div class="game-row">
                        <img class="champ-icon" src="{icon}" width="40"/>
                        <div class="col">
                            <b>{b['label']}</b><br/>
                            <span style="color:{result_color};font-weight:700;">{b['value']}</span><br/>
                            {b['champion']} · {b['date_str']}
                        </div>
                        </div>""",
                    unsafe_allow_html=True,
                )

    with section_card(
        "home-recap", "Season Recap",
        "A shareable summary image of your headline stats for the current queue filter.",
        icon="🖼️",
    ):
        if st.button("Generate season recap"):
            champ_wr_all = win_rate_by(filtered_df, "champion")
            top_champ_row = champ_wr_all.iloc[0] if not champ_wr_all.empty else None
            best_mu = best_matchup_overall(filtered_df)
            solo_entry = solo

            recap_data = {
                "name": f"{GAME_NAME}#{TAG_LINE}",
                "app_title": APP_TITLE,
                "scope_label": queue_filter,
                "games": games,
                "win_rate": wr,
                "longest_win_streak": longest_streak(filtered_df, want_win=True),
                "avg_kda": avgs["kda"],
                "rank": f"{solo_entry['tier'].title()} {solo_entry['rank']} ({solo_entry['leaguePoints']} LP)" if solo_entry else None,
                "top_champion": top_champ_row["champion"] if top_champ_row is not None else "N/A",
                "top_champion_win_rate": top_champ_row["win_rate"] if top_champ_row is not None else 0,
                "top_champion_games": top_champ_row["games"] if top_champ_row is not None else 0,
                "top_champion_icon_url": (
                    ddragon.champion_icon_url(top_champ_row["champion"], version) if top_champ_row is not None else None
                ),
                "best_matchup": (
                    f"{best_mu['champion']} vs {best_mu['opponent']} — {best_mu['win_rate']}% ({best_mu['games']}g)"
                    if best_mu else None
                ),
            }
            st.session_state.recap_bytes = recap.build_recap_image(recap_data, accent)

        if st.session_state.recap_bytes:
            st.image(st.session_state.recap_bytes, width=360)
            st.download_button(
                "Download recap PNG", data=st.session_state.recap_bytes,
                file_name="season_recap.png", mime="image/png",
            )

    with section_card("home-recent", "Recent Games", icon="🕹️"):
        fc1, fc2 = st.columns([3, 1])
        champ_filter = fc1.multiselect(
            "Filter by champion", sorted(filtered_df["champion"].dropna().unique()),
            placeholder="All champions",
        )
        result_filter = fc2.selectbox("Result", ["All", "Wins", "Losses"])

        recent_scope = filtered_df
        if champ_filter:
            recent_scope = recent_scope[recent_scope["champion"].isin(champ_filter)]
        if result_filter == "Wins":
            recent_scope = recent_scope[recent_scope["win"]]
        elif result_filter == "Losses":
            recent_scope = recent_scope[~recent_scope["win"]]

        if recent_scope.empty:
            st.caption("No games match this filter.")
        else:
            recent_games_feed(recent_scope, n=20)


# ==================== Page: Champions ====================
def page_champions():
    pool = champion_pool_concentration(filtered_df, top_n=3)
    render_hero(
        "Champions", "Win rates across everything you've played",
        stats=[
            ("Unique Champs", pool["unique_champions"]),
            ("Top 3 Share", f"{pool['top_n_share']}%"),
        ],
    )
    champ_wr = win_rate_by(filtered_df, "champion")
    if champ_wr.empty:
        st.write("No data.")
        return

    roles_by_champ = primary_roles(filtered_df)

    with section_card("champs-grid", "Champion Pool", icon="🧙", featured=True):
        sort_col, filter_col = st.columns([3, 2])
        with sort_col:
            sort_choice = st.radio(
                "Sort by", ["Games played", "Win rate", "Alphabetical"], horizontal=True,
            )
        with filter_col:
            role_options = ["All roles"] + [
                r for r in ROLE_ORDER if r in set(filtered_df["role_label"])
            ]
            role_choice = st.selectbox("Role", role_options, index=0)

        scoped = filtered_df if role_choice == "All roles" else \
            filtered_df[filtered_df["role_label"] == role_choice]
        champ_wr_scoped = win_rate_by(scoped, "champion")
        if champ_wr_scoped.empty:
            st.caption(f"No champions played as {role_choice} in this queue filter.")
        else:
            if sort_choice == "Games played":
                champ_wr_sorted = champ_wr_scoped.sort_values("games", ascending=False)
            elif sort_choice == "Win rate":
                champ_wr_sorted = champ_wr_scoped.sort_values("win_rate", ascending=False)
            else:
                champ_wr_sorted = champ_wr_scoped.sort_values("champion")

            # Role column comes from the *scoped* data, so filtering to Support
            # shows Support for a champion you also play elsewhere.
            scoped_roles = primary_roles(scoped)
            champ_wr_sorted = champ_wr_sorted.copy()
            champ_wr_sorted.insert(
                1, "role", champ_wr_sorted["champion"].map(scoped_roles).fillna("Unknown")
            )

            champion_card_grid(
                champ_wr_sorted, max_cards=len(champ_wr_sorted), roles=scoped_roles
            )
            percent_table(champ_wr_sorted, hide_index=True)

    with section_card(
        "champs-mastery", "Mastery vs. Win Rate",
        "Does more invested time actually mean better results, or just more comfort?",
        icon="⭐", featured=True,
    ):
        if not mastery:
            st.caption(
                "No champion mastery data available. This uses Riot's separate "
                "champion-mastery-v4 endpoint — if it's consistently empty, your "
                "installed `riotwatcher` may predate its puuid-based methods "
                "(`pip install -U riotwatcher` fixes that)."
            )
        else:
            champs_catalog = get_champions_catalog(version)
            mastery_rows = []
            for m in mastery:
                champ_info = champs_catalog.get(m.get("championId"))
                if not champ_info:
                    continue
                mastery_rows.append({
                    "champion": champ_info["id"],
                    "Mastery level": m.get("championLevel"),
                    "Mastery points": m.get("championPoints", 0),
                })
            if not mastery_rows:
                st.caption("Mastery data loaded but no champions could be matched to Data Dragon.")
            else:
                mastery_df = pd.DataFrame(mastery_rows)
                # Inner join: only champions you've both played (in the current
                # queue filter) and have mastery on, since the point is the
                # correlation between the two.
                merged = mastery_df.merge(
                    champ_wr[["champion", "games", "win_rate"]], on="champion", how="inner"
                )
                if merged.empty:
                    st.caption("No overlap between your mastery list and games in this queue filter.")
                else:
                    fig = px.scatter(
                        merged, x="Mastery points", y="win_rate", size="games",
                        hover_name="champion", hover_data=["games", "Mastery level"],
                        labels={"win_rate": "Win rate (%)"},
                        title="Mastery points vs. win rate (bubble size = games played)",
                    )
                    fig.update_layout(template=PLOT_TEMPLATE)
                    st.plotly_chart(fig, use_container_width=True)
                    percent_table(
                        merged.sort_values("Mastery points", ascending=False),
                        hide_index=True,
                    )
                    st.caption(
                        "Mastery points accumulate across your whole account history, while "
                        "win rate here is scoped to the matches this dashboard has loaded — "
                        "so treat this as a rough correlation, not a controlled comparison."
                    )

    role_wr = win_rate_by_role(filtered_df)
    if not role_wr.empty:
        with section_card("champs-role", "Win Rate by Role", icon="🛡️"):
            fig = px.bar(
                role_wr, x="role_label", y="win_rate", hover_data=["games", "wins"],
                labels={"win_rate": "Win rate (%)", "role_label": "Role"},
                title="Win Rate by Role",
            )
            # Lane order, not Plotly's default alphabetical/first-seen order.
            fig.update_layout(
                template=PLOT_TEMPLATE,
                xaxis=dict(categoryorder="array", categoryarray=list(role_wr["role_label"])),
            )
            st.plotly_chart(fig, use_container_width=True)
            percent_table(role_wr, hide_index=True)

    with section_card(
        "champs-pool", "Pool Concentration",
        "One-tricking vs. spreading thin — neither is inherently better, it just "
        "shapes what you should practice.",
        icon="🎲",
    ):
        metric_grid([
            ("Unique Champions", pool["unique_champions"]),
            ("Games", pool["total_games"]),
            (f"Top {pool['top_n']} Share", f"{pool['top_n_share']}%"),
        ], cols_per_row=3)


# ==================== Page: Trends ====================
def page_trends():
    recent_n = 10
    r_kda, a_kda = recent_vs_alltime(filtered_df, "kda", recent_n)
    r_cs, a_cs = recent_vs_alltime(filtered_df, "cs_per_min", recent_n)
    r_vis, a_vis = recent_vs_alltime(filtered_df, "vision_score", recent_n)
    render_hero(
        "Trends", "Are you trending up or down?",
        stats=[("Recent KDA", r_kda), ("Recent CS/min", r_cs), ("Recent Vision", r_vis)],
    )

    with section_card(
        "trends-recent", "Recent Form", f"Last {recent_n} games vs. all-time average",
        icon="📉", featured=True,
    ):
        c1, c2, c3 = st.columns(3)
        c1.metric("Recent KDA", r_kda, delta=round(r_kda - a_kda, 2))
        c2.metric("Recent CS/min", r_cs, delta=round(r_cs - a_cs, 2))
        c3.metric("Recent Vision", r_vis, delta=round(r_vis - a_vis, 2))

    with section_card("trends-overtime", "Performance Over Time", icon="📈"):
        st.plotly_chart(pretty_trend_chart(filtered_df, "kda", "KDA Over Time"), use_container_width=True)
        st.plotly_chart(pretty_trend_chart(filtered_df, "cs_per_min", "CS / Min Over Time"), use_container_width=True)
        st.plotly_chart(pretty_trend_chart(filtered_df, "vision_score", "Vision Score Over Time"), use_container_width=True)
        st.plotly_chart(pretty_trend_chart(filtered_df, "kill_participation", "Kill Participation Over Time"), use_container_width=True)
        st.plotly_chart(pretty_trend_chart(filtered_df, "damage_share", "Damage Share Over Time"), use_container_width=True)

    with section_card(
        "trends-vision", "Vision & Utility",
        "Ward counts (not locations — Riot's API doesn't expose ward placement "
        "positions) and crowd control score, which kills/deaths alone undersell.",
        icon="👁️",
    ):
        vis = vision_summary(filtered_df)
        metric_grid([
            ("Avg Wards Placed", vis["wards_placed"]),
            ("Avg Wards Killed", vis["wards_killed"]),
            ("Avg Control Wards Bought", vis["vision_wards_bought"]),
            ("Avg CC Score", averages(filtered_df)["cc_score"]),
        ], cols_per_row=2)
        st.plotly_chart(pretty_trend_chart(filtered_df, "wards_placed", "Wards Placed Over Time"), use_container_width=True)
        st.plotly_chart(pretty_trend_chart(filtered_df, "wards_killed", "Wards Killed Over Time"), use_container_width=True)
        st.plotly_chart(pretty_trend_chart(filtered_df, "cc_score", "Crowd Control Score Over Time"), use_container_width=True)

    with section_card("trends-tilt", "Tilt Check", "When do you actually play well?", icon="🕐"):
        hour_wr = performance_by_hour(filtered_df)
        if not hour_wr.empty:
            # Keep the numeric `hour` for ordering and add a display column —
            # sorting on "10 AM" as a string would put it after "1 PM".
            hour_wr = hour_wr.copy()
            hour_wr["hour_display"] = hour_wr["hour"].map(hour_label)
            fig = px.bar(
                hour_wr, x="hour_display", y="win_rate", hover_data=["games", "wins"],
                labels={"win_rate": "Win rate (%)", "hour_display": "Time of day"},
                title="Win Rate by Time of Day",
            )
            fig.update_layout(template=PLOT_TEMPLATE)
            fig.update_xaxes(categoryorder="array",
                             categoryarray=list(hour_wr["hour_display"]))
            st.plotly_chart(fig, use_container_width=True)

        weekday_wr = performance_by_weekday(filtered_df)
        if not weekday_wr.empty:
            fig = px.bar(
                weekday_wr, x="weekday", y="win_rate", hover_data=["games", "wins"],
                labels={"win_rate": "Win rate (%)", "weekday": "Day of week"},
                title="Win Rate by Day of Week",
            )
            fig.update_layout(template=PLOT_TEMPLATE)
            st.plotly_chart(fig, use_container_width=True)

        after_wr = win_rate_after_result(filtered_df)
        if not after_wr.empty:
            fig = px.bar(
                after_wr, x="prev_result", y="win_rate", hover_data=["games", "wins"],
                labels={"win_rate": "Win rate (%)", "prev_result": ""},
                title="Win Rate After a Win vs. After a Loss",
            )
            fig.update_layout(template=PLOT_TEMPLATE)
            st.plotly_chart(fig, use_container_width=True)
            st.caption(
                "Approximate — uses games in this queue filter sorted by time as a stand-in "
                "for 'the previous game,' which may not always be truly back-to-back."
            )

    with section_card(
        "trends-quickwins", "Odds & Ends",
        "Small things that are free to compute from games already loaded.",
        icon="🎲",
    ):
        st.markdown("**Blue side vs. red side**")
        sides = side_win_rate(filtered_df, min_games=3)
        if sides.empty:
            st.caption("Need 3+ games on a side.")
        else:
            percent_table(sides, hide_index=True)
            st.caption(
                "Side is assigned by matchmaking, not chosen — so this is trivia about "
                "your sample rather than something to act on."
            )

        st.markdown("**Surrenders**")
        ff = surrender_summary(filtered_df)
        if not ff["surrendered_games"]:
            st.caption("None of your loaded games ended in a surrender.")
        else:
            metric_grid([
                ("Games ending in FF", f"{ff['surrender_rate']}%"),
                ("Won by FF", ff["wins_by_surrender"]),
                ("Lost by FF", ff["losses_by_surrender"]),
                ("Early FF (remake)", f"{ff['early_surrender_rate']}%"),
            ], cols_per_row=2)

        st.markdown("**Flash on D vs. F**")
        flash = flash_slot_win_rate(filtered_df, min_games=3)
        if flash.empty:
            st.caption("Not enough games with Flash on either key yet.")
        else:
            percent_table(flash, hide_index=True)
            st.caption(
                "The long-running keybind argument, answered from your own games. Any gap "
                "here is far more likely to be habit and sample size than the key itself."
            )

        st.markdown("**Pings**")
        pings = ping_summary(filtered_df)
        if pings.empty:
            st.caption(
                "No ping data in these matches. Riot only started including ping counters "
                "on newer matches, so an older cache legitimately won't have them."
            )
        else:
            fig = px.bar(
                pings.head(8), x="ping", y="per_game",
                labels={"per_game": "Per game", "ping": ""},
            )
            fig.update_layout(template=PLOT_TEMPLATE)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(pings, use_container_width=True, hide_index=True)

    with section_card("trends-patch-runes", "Patch & Rune Tendencies", icon="🧬"):
        st.markdown("**Win Rate by Patch**")
        st.caption("What patch each game was played on, grouped chronologically.")
        render_patch_win_rate(filtered_df, "Win Rate by Patch (all champions)")

        st.markdown("**Rune & Summoner Spell Tendencies**")
        st.caption("Keystone and summoner spell combos, straight from already-loaded match data — no extra API calls.")
        # Sitewide version of the same problem the deep-dive has: pooled
        # across roles, this mostly measures which role you play most. Smite
        # is in every jungle game, so a jungle-heavy history makes
        # Flash+Smite look like a universally strong combo rather than a
        # positional requirement.
        trend_roles = [r for r in ROLE_ORDER
                       if r in set(filtered_df.get("role_label", pd.Series(dtype=str)).dropna())]
        role_counts = filtered_df["role_label"].value_counts() if "role_label" in filtered_df else {}
        rune_options = ["All roles"] + [f"{r} ({role_counts.get(r, 0)})" for r in trend_roles]
        rune_pick = st.selectbox(
            "Role", rune_options, key="trends-rune-role",
            help="Keystones and summoner spells are largely dictated by role. "
                 "Scoping to one makes the comparison meaningful.",
        )
        rune_role = None if rune_pick == "All roles" else rune_pick.rsplit(" (", 1)[0]
        rune_df = role_scope(filtered_df, rune_role)
        render_keystone_win_rates(rune_df)
        st.markdown("&nbsp;", unsafe_allow_html=True)
        render_summoner_combo_win_rates(rune_df)
        if rune_role is None and len(trend_roles) > 1:
            st.caption(
                "Pooled across every role. Summoner spells especially are "
                "positional — Smite appears in every jungle game — so this is "
                "closer to a picture of what you play than of what works."
            )

    with section_card(
        "trends-deep", "Deep Match Analytics",
        "Gold curve, objectives, lane diff, death/kill map — needs a timeline API call per "
        "game, so it's scoped to your last 50 games in this queue filter and loaded on demand.",
        icon="🗺️", featured=True,
    ):
        if st.button("Load deep match analytics (last 50 games)"):
            recent_df = filtered_df.sort_values("game_creation", ascending=False).head(50)
            recent_match_ids = recent_df["match_id"].tolist()
            progress_bar = st.progress(0.0, text=f"Fetching timelines for {len(recent_match_ids)} games...")

            def _on_progress(done, total):
                progress_bar.progress(done / total, text=f"Fetching timelines... {done}/{total}")

            timelines = client.fetch_timelines(recent_match_ids, use_cache=use_cache, on_progress=_on_progress)
            progress_bar.empty()
            gdf = gold_curve(timelines, puuid)
            st.session_state.gold_cache["__general__"] = gold_curve_summary(gdf, filtered_df)

            st.session_state.objectives_cache["__general__"] = objective_participation_summary(
                timelines, puuid
            )

            opp_map = dict(zip(recent_df["match_id"], recent_df["opponent_puuid"]))
            diff_df = gold_diff_curve(timelines, opp_map, puuid)
            st.session_state.gold_diff_cache["__general__"] = gold_diff_summary(diff_df)
            # Same timelines, same opponent map — the CS differential is free
            # here, no extra API calls beyond what gold diff already needed.
            cs_df = cs_diff_curve(timelines, opp_map, puuid)
            st.session_state.cs_diff_cache["__general__"] = cs_diff_summary(cs_df)

            st.session_state.heatmap_cache["__general__"] = death_kill_positions(timelines, puuid)

            # Team gold-difference series per game, which the empirical win
            # probability lookup is built from. Team membership is joined via
            # the cached match JSON (free — already on disk) rather than
            # assuming participant ids 1-5 are always team 100.
            series_by_match = {}
            for mid, tl in timelines.items():
                try:
                    match_json = client.get_match(mid, use_cache=True)
                except Exception:
                    continue
                teams = participant_teams(tl, match_json)
                my_pid = next(
                    (p["participantId"] for p in tl.get("info", {}).get("participants", [])
                     if p.get("puuid") == puuid),
                    None,
                )
                my_team = teams.get(my_pid) if my_pid else None
                if my_team is None:
                    continue
                series_by_match[mid] = team_gold_diff_series(tl, teams, my_team)
            st.session_state.wp_series = series_by_match
            wins_by_match = dict(zip(recent_df["match_id"], recent_df["win"]))
            st.session_state.wp_table = build_win_probability_table(series_by_match, wins_by_match)

        general_gold = st.session_state.gold_cache.get("__general__")
        if general_gold is not None and not general_gold.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=general_gold["minute"], y=general_gold["avg_gold_win"], mode="lines+markers",
                name="Wins", line=dict(color="#2DD4BF", width=3),
            ))
            fig.add_trace(go.Scatter(
                x=general_gold["minute"], y=general_gold["avg_gold_loss"], mode="lines+markers",
                name="Losses", line=dict(color="#FB7185", width=3),
            ))
            fig.update_layout(
                template=PLOT_TEMPLATE, title="Average Gold — Wins vs. Losses",
                xaxis_title="Minute", yaxis_title="Gold",
            )
            st.plotly_chart(fig, use_container_width=True)

        general_diff = st.session_state.gold_diff_cache.get("__general__")
        if general_diff is not None and not general_diff.empty:
            fig = px.bar(
                general_diff, x="minute", y="avg_diff",
                labels={"avg_diff": "Gold diff vs. lane opponent", "minute": "Minute"},
                title="Average Gold Lead/Deficit vs. Lane Opponent",
            )
            fig.update_layout(template=PLOT_TEMPLATE)
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Positive = ahead of your direct lane opponent (same role, other team) at that checkpoint.")

        general_cs_diff = st.session_state.cs_diff_cache.get("__general__")
        if general_cs_diff is not None and not general_cs_diff.empty:
            fig = px.bar(
                general_cs_diff, x="minute", y="avg_diff",
                labels={"avg_diff": "CS diff vs. lane opponent", "minute": "Minute"},
                title="Average CS Lead/Deficit vs. Lane Opponent",
            )
            fig.update_layout(template=PLOT_TEMPLATE)
            st.plotly_chart(fig, use_container_width=True)
            st.caption(
                "Often a cleaner read on lane phase than the gold chart above — gold also "
                "swings on kills, plates, and bounties that aren't strictly about laning."
            )

        general_obj = st.session_state.objectives_cache.get("__general__")
        if general_obj is not None and not general_obj.empty:
            st.markdown("*Objective participation — % of games you were in on at least one*")
            obj_pct = objective_participation_rates(general_obj)
            oc1, oc2, oc3, oc4 = st.columns(4)
            oc1.metric("Dragons", f"{obj_pct['dragons']}%")
            oc2.metric("Barons", f"{obj_pct['barons']}%")
            oc3.metric("Heralds", f"{obj_pct['heralds']}%")
            oc4.metric("Towers", f"{obj_pct['towers']}%")
            st.caption("Counts you as a kill or an assist on that objective.")

        general_heatmap = st.session_state.heatmap_cache.get("__general__")
        if general_heatmap is not None:
            st.markdown("*Death & kill locations (last 50 games loaded)*")
            render_position_heatmap(general_heatmap, "Death & Kill Map")

    if st.session_state.wp_series:
        with section_card(
            "trends-winprob", "Game Review — Win Probability",
            "Where a game actually turned, so replay review means watching two or three "
            "timestamps instead of forty minutes.",
            icon="📉", featured=True,
        ):
            render_win_probability_review(filtered_df)


# ==================== Page: Champion Deep-Dive ====================
def page_deepdive():
    champs_played = sorted(filtered_df["champion"].dropna().unique())
    if not champs_played:
        st.markdown("### Champion Deep-Dive")
        st.info("No champions played in this queue filter yet.")
        return

    # Deep-linkable: ?champion=Ahri opens this page with Ahri pre-selected,
    # so a specific breakdown can be bookmarked or shared. Matched
    # case-insensitively against played champions, and silently ignored if
    # the name isn't in this queue filter rather than erroring.
    query_champ = st.query_params.get("champion")
    default_index = 0
    if query_champ:
        lookup = {c.lower(): i for i, c in enumerate(champs_played)}
        default_index = lookup.get(query_champ.lower(), 0)

    selected = st.selectbox("Pick a champion", champs_played, index=default_index)
    # Keep the URL in sync with the picker so copying the address bar always
    # links to whatever is currently on screen.
    if st.query_params.get("champion") != selected:
        st.query_params["champion"] = selected

    # ---- Role scope for the whole page ----
    # One control rather than one per card. Runes, summoner spells, builds and
    # matchups are all role-dependent in the same way: a champion played in
    # two positions runs different keystones, buys different items and faces
    # different opponents, so pooling them produces a table that describes
    # neither. Scoping `sub` here means every card below inherits it.
    #
    # Counts are shown in the options because the tradeoff is always the same
    # — narrowing to a role buys comparability and costs sample size, and you
    # can only judge that if you can see what you're left with.
    champ_roles = roles_played(filtered_df, selected)
    role_filter = None
    if len(champ_roles) > 1:
        counts = filtered_df[filtered_df["champion"] == selected]["role_label"].value_counts()
        options = ["All roles"] + [f"{r} ({counts.get(r, 0)})" for r in champ_roles]
        picked = st.selectbox(
            "Role", options, key=f"dd-role-{selected}",
            help="Scopes everything on this page — matchups, runes, summoner "
                 "spells and builds. Numbers in brackets are games in that role.",
        )
        role_filter = None if picked == "All roles" else picked.rsplit(" (", 1)[0]

    scoped_df = role_scope(filtered_df, role_filter)
    sub = scoped_df[scoped_df["champion"] == selected]
    if role_filter and sub.empty:
        st.warning(f"No {selected} games in {role_filter}.")
        return
    g, w, wr2 = overall_win_rate(sub)

    # Role split for this champion. Shown as a hero chip when it's basically
    # a one-role champion for you, and broken out properly in its own card
    # below when you actually play it in more than one position.
    champ_role_wr = role_split_for_champion(filtered_df, selected)
    champ_roles = list(champ_role_wr["role_label"]) if not champ_role_wr.empty else []
    role_chip = ""
    if champ_roles:
        top_role = champ_role_wr.sort_values("games", ascending=False).iloc[0]["role_label"]
        chip_text = top_role if len(champ_roles) == 1 else f"{top_role} +{len(champ_roles) - 1}"
        role_chip = (
            '<div class="hero-stat-chip"><div class="chip-label">Role</div>'
            f'<div class="chip-value">{chip_text}</div></div>'
        )

    # Intentionally the one hero on the site that keeps its own splash-art
    # background — this one's champion-specific (whichever you pick), not
    # the generic theme banner, so a real photo behind it is the point
    # rather than something that clashes with a different background image.
    splash = ddragon.champion_splash_url(selected)
    st.markdown(
        f"""<div class="hero-banner" style="background-image:
                linear-gradient(rgba(5,8,12,0.3), rgba(5,8,12,0.88)), url('{splash}');
                background-size: cover; background-position: center 20%; border: none;">
                <div class="hero-banner-body">
                    <div class="hero-banner-eyebrow" style="color:#F4F4F7;opacity:0.75;">Champion Deep-Dive</div>
                    <div class="hero-banner-title" style="color:#F4F4F7;background:none;-webkit-text-fill-color:#F4F4F7;">{selected}</div>
                    <div class="hero-stat-row">
                        <div class="hero-stat-chip"><div class="chip-label">Games</div><div class="chip-value">{g}</div></div>
                        <div class="hero-stat-chip"><div class="chip-label">Wins</div><div class="chip-value">{w}</div></div>
                        <div class="hero-stat-chip"><div class="chip-label">Win Rate</div><div class="chip-value">{wr2}%</div></div>
                        {role_chip}
                    </div>
                </div>
            </div>""",
        unsafe_allow_html=True,
    )

    with section_card("dd-performance", f"{selected} — Performance", icon="📈", featured=True):
        st.plotly_chart(pretty_trend_chart(sub, "kda", f"{selected} — KDA Trend"), use_container_width=True)

        champ_kd_wr = kill_diff_win_rate(sub)
        if not champ_kd_wr.empty:
            fig = px.bar(
                champ_kd_wr, x="kill_diff_bucket", y="win_rate", hover_data=["games", "wins"],
                labels={"win_rate": "Win rate (%)", "kill_diff_bucket": "Kills minus deaths"},
                title=f"{selected} — Win Rate by Kill Differential",
            )
            fig.update_layout(template=PLOT_TEMPLATE)
            st.plotly_chart(fig, use_container_width=True)

        st.markdown(f"**{selected} — Win Rate by Patch**")
        render_patch_win_rate(sub, f"{selected} — Win Rate by Patch")

        if len(champ_roles) > 1:
            st.markdown(f"**{selected} — By Role**")
            st.caption(
                f"You play {selected} in {len(champ_roles)} positions, so the numbers "
                "elsewhere on this page blend them together. Here they're split out."
            )
            fig = px.bar(
                champ_role_wr, x="role_label", y="win_rate", hover_data=["games", "wins"],
                labels={"win_rate": "Win rate (%)", "role_label": "Role"},
            )
            fig.update_layout(
                template=PLOT_TEMPLATE,
                xaxis=dict(categoryorder="array", categoryarray=list(champ_role_wr["role_label"])),
            )
            st.plotly_chart(fig, use_container_width=True)
            percent_table(champ_role_wr, hide_index=True)
        elif champ_roles:
            st.markdown(f"**Role:** {champ_roles[0]} — all {g} games.")

        st.markdown(f"**{selected} — Player Profile**")
        render_performance_radar(sub, st.session_state.objectives_cache.get(selected), label=selected)

        st.markdown(f"**{selected} — Vision & Utility**")
        champ_vis = vision_summary(sub)
        champ_avgs = averages(sub)
        metric_grid([
            ("Avg Wards Placed", champ_vis["wards_placed"]),
            ("Avg Wards Killed", champ_vis["wards_killed"]),
            ("Avg Control Wards", champ_vis["vision_wards_bought"]),
            ("Avg CC Score", champ_avgs["cc_score"]),
        ], cols_per_row=2)

    with section_card("dd-matchups-runes", "Matchups & Runes", icon="⚔️"):
        st.markdown("**Matchups faced (lane opponent)**")
        # `scoped_df` already carries the page-level role filter, so no
        # separate role argument is needed here.
        mu = matchup_win_rate(scoped_df, selected)
        if mu.empty:
            st.caption("No matchup data yet for this champion in that role.")
        else:
            percent_table(mu, hide_index=True)

        role_note = f" — {role_filter}" if role_filter else ""
        st.markdown(f"**{selected}{role_note} — Rune & Summoner Spell Win Rates**")
        render_keystone_win_rates(sub)
        st.markdown("&nbsp;", unsafe_allow_html=True)
        render_summoner_combo_win_rates(sub)
        if role_filter is None and len(champ_roles) > 1:
            st.caption(
                f"All {len(champ_roles)} roles pooled. You play {selected} in more "
                "than one position, and runes, spells, builds and matchups all "
                "differ by role — pick one above for a comparable set."
            )

    with section_card("dd-skins-builds", "Skins & Builds", icon="🎨"):
        st.markdown("**Most played skins**")
        skins_df = skin_usage(scoped_df, selected)
        if skins_df.empty:
            st.caption("No skin data.")
        else:
            skin_names = get_skin_names(selected, version)
            top_skins = skins_df.head(4)
            cols = st.columns(len(top_skins))
            for i, (_, row) in enumerate(top_skins.iterrows()):
                skin_num = int(row["skin_id"])
                name = skin_names.get(skin_num, "Classic" if skin_num == 0 else f"Skin {skin_num}")
                thumb = ddragon.champion_splash_url(selected, skin_num)
                cols[i].image(
                    thumb, caption=f"{name} — {row['games']}g ({row['win_rate']}%)",
                    use_container_width=True,
                )

        st.markdown("**Most common final builds**")
        builds_df = build_win_rate(scoped_df, selected)
        if builds_df.empty:
            st.caption("No build data.")
        else:
            for _, row in builds_df.head(5).iterrows():
                items_html = "".join(
                    f'<img class="item-icon" src="{ddragon.item_icon_url(i, version)}" width="32"/>'
                    for i in row["build"]
                )
                wr_color = "#2DD4BF" if row["win_rate"] >= 50 else "#FB7185"
                st.markdown(
                    f"""<div class="build-row">
                        {items_html}
                        <span>{row['games']}g</span>
                        <span style="color:{wr_color};font-weight:700;">{row['win_rate']}%</span>
                        </div>""",
                    unsafe_allow_html=True,
                )
            st.caption("Final inventory at game end — order-independent. See Build Order below for purchase sequence.")
            st.caption(f"⚠︎ {SELECTION_BIAS_NOTE}")

    with section_card("dd-buildorder", "Build Order", "Purchase sequence for a specific game", icon="🛒"):
        game_options = {
            f"{row['game_creation'].strftime('%b %d, %I:%M %p')} — {'Win' if row['win'] else 'Loss'}": row["match_id"]
            for _, row in sub.sort_values("game_creation", ascending=False).iterrows()
        }
        picked_label = st.selectbox("View purchase order for a specific game", list(game_options.keys()))
        picked_match_id = game_options[picked_label]

        timeline = client.get_timeline(picked_match_id, use_cache=use_cache)
        if timeline is None:
            st.caption("No timeline data available for this match (older or non-standard match types sometimes lack it).")
        else:
            purchases = parse_purchase_events(timeline, puuid)
            if not purchases:
                st.caption("No purchase events found for this game.")
            else:
                items_html = "".join(
                    f"""<div style="text-align:center;">
                        <img class="item-icon" src="{ddragon.item_icon_url(e['item_id'], version)}" width="32"/><br/>
                        <span style="font-size:0.75rem;">{e['timestamp_min']}m</span>
                        </div>"""
                    for e in purchases
                )
                st.markdown(
                    f'<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-start;">{items_html}</div>',
                    unsafe_allow_html=True,
                )

    with section_card(
        "dd-timeline", "Timeline Analytics",
        "Opening build, gold curve, objectives, lane diff — requires fetching a timeline "
        "per game on this champion, not pulled automatically since it multiplies API calls.",
        icon="⏱️", featured=True,
    ):
        if st.button(f"Load timeline analytics for {selected}"):
            match_ids = sub["match_id"].tolist()
            progress_bar = st.progress(0.0, text=f"Fetching timelines for {len(match_ids)} games...")

            def _on_progress(done, total):
                progress_bar.progress(done / total, text=f"Fetching timelines... {done}/{total}")

            timelines = client.fetch_timelines(match_ids, use_cache=use_cache, on_progress=_on_progress)
            progress_bar.empty()
            items_data = get_items_catalog(version)
            openings = {}
            skill_orders = {}
            skill_unreliable = 0
            for match_id, tl in timelines.items():
                purchases = parse_purchase_events(tl, puuid)
                openings[match_id] = opening_build(purchases, items_data, n=3)
                # parse_skill_level_ups returns None when Riot's duplicate-event
                # bug makes a game's skill data impossible; count those instead
                # of quietly dropping them.
                slots = parse_skill_level_ups(tl, puuid)
                if slots is None:
                    skill_unreliable += 1
                else:
                    skill_orders[match_id] = skill_max_order(slots)
            st.session_state.openings_cache[selected] = openings
            st.session_state.skill_order_cache[selected] = {
                "orders": skill_orders,
                "unreliable": skill_unreliable,
            }
            gdf = gold_curve(timelines, puuid)
            st.session_state.gold_cache[selected] = gold_curve_summary(gdf, sub)
            st.session_state.objectives_cache[selected] = objective_participation_summary(
                timelines, puuid
            )
            opp_map = dict(zip(sub["match_id"], sub["opponent_puuid"]))
            diff_df = gold_diff_curve(timelines, opp_map, puuid)
            st.session_state.gold_diff_cache[selected] = gold_diff_summary(diff_df)
            cs_df = cs_diff_curve(timelines, opp_map, puuid)
            st.session_state.cs_diff_cache[selected] = cs_diff_summary(cs_df)

            st.session_state.heatmap_cache[selected] = death_kill_positions(timelines, puuid)

            # Which of these games were still close at 15 min — used by the
            # "even games only" filter below, so build/skill comparisons can
            # control for game state instead of rewarding whatever you happen
            # to build while already stomping.
            even_flags = {}
            for mid, tl in timelines.items():
                try:
                    match_json = client.get_match(mid, use_cache=True)
                except Exception:
                    continue
                teams_map = participant_teams(tl, match_json)
                my_pid = next(
                    (p["participantId"] for p in tl.get("info", {}).get("participants", [])
                     if p.get("puuid") == puuid),
                    None,
                )
                my_team = teams_map.get(my_pid) if my_pid else None
                if my_team is None:
                    continue
                even_flags[mid] = was_even_at(team_gold_diff_series(tl, teams_map, my_team))
            st.session_state.even_cache[selected] = even_flags

        # Shared control for the confounded win-rate sections below.
        champ_even = st.session_state.even_cache.get(selected, {})
        even_only = False
        if champ_even:
            even_ids = [m for m, flag in champ_even.items() if flag]
            even_only = st.checkbox(
                f"Even games only (within {EVEN_GOLD_THRESHOLD:,} gold at 15 min) — {len(even_ids)} games",
                value=False,
                help="Controls for game state, so these compare choices rather than "
                     "rewarding whatever you build when you're already ahead.",
            )
            sub_for_choices = sub[sub["match_id"].isin(even_ids)] if even_only else sub
        else:
            sub_for_choices = sub

        champ_openings = st.session_state.openings_cache.get(selected)
        if champ_openings:
            st.markdown("*Most common opening build (win rate by first 3 core items rushed)*")
            opening_wr = opening_build_win_rate(sub_for_choices, champ_openings, selected)
            if opening_wr.empty:
                st.caption("No opening-build data yet — try loading again, or this champion's games may lack timeline data.")
            else:
                for _, row in opening_wr.head(5).iterrows():
                    items_html = "".join(
                        f'<img class="item-icon" src="{ddragon.item_icon_url(i, version)}" width="32"/>'
                        for i in row["opening"]
                    )
                    wr_color = "#2DD4BF" if row["win_rate"] >= 50 else "#FB7185"
                    st.markdown(
                        f"""<div class="build-row">
                            {items_html}
                            <span>{row['games']}g</span>
                            <span style="color:{wr_color};font-weight:700;">{row['win_rate']}%</span>
                            </div>""",
                        unsafe_allow_html=True,
                    )

        champ_skills = st.session_state.skill_order_cache.get(selected)
        if champ_skills:
            st.markdown("*Skill max order (win rate by which abilities you maxed, in order)*")
            skill_wr = skill_order_win_rate(sub_for_choices, champ_skills["orders"], selected)
            if skill_wr.empty:
                st.caption("No usable skill-order data for this champion yet.")
            else:
                for _, row in skill_wr.head(5).iterrows():
                    path = " › ".join(row["skill_order"])
                    wr_color = "#2DD4BF" if row["win_rate"] >= 50 else "#FB7185"
                    st.markdown(
                        f"""<div class="build-row">
                            <span style="font-weight:700;font-size:1.05rem;">{path}</span>
                            <span>{row['games']}g</span>
                            <span style="color:{wr_color};font-weight:700;">{row['win_rate']}%</span>
                            </div>""",
                        unsafe_allow_html=True,
                    )
            if champ_skills["unreliable"]:
                st.caption(
                    f"{champ_skills['unreliable']} game(s) excluded — Riot's match timeline has an "
                    "open bug (since patch 15.17) that duplicates skill-level-up events, making some "
                    "games report more skill points than the game actually allows. Those are dropped "
                    "rather than charted wrong."
                )

        champ_gold = st.session_state.gold_cache.get(selected)
        if champ_gold is not None and not champ_gold.empty:
            st.markdown(f"*{selected} — average gold, wins vs. losses*")
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=champ_gold["minute"], y=champ_gold["avg_gold_win"], mode="lines+markers",
                name="Wins", line=dict(color="#2DD4BF", width=3),
            ))
            fig.add_trace(go.Scatter(
                x=champ_gold["minute"], y=champ_gold["avg_gold_loss"], mode="lines+markers",
                name="Losses", line=dict(color="#FB7185", width=3),
            ))
            fig.update_layout(template=PLOT_TEMPLATE, xaxis_title="Minute", yaxis_title="Gold")
            st.plotly_chart(fig, use_container_width=True)

        champ_diff = st.session_state.gold_diff_cache.get(selected)
        if champ_diff is not None and not champ_diff.empty:
            st.markdown(f"*{selected} — gold lead/deficit vs. lane opponent*")
            fig = px.bar(
                champ_diff, x="minute", y="avg_diff",
                labels={"avg_diff": "Gold diff", "minute": "Minute"},
            )
            fig.update_layout(template=PLOT_TEMPLATE)
            st.plotly_chart(fig, use_container_width=True)

        champ_cs_diff = st.session_state.cs_diff_cache.get(selected)
        if champ_cs_diff is not None and not champ_cs_diff.empty:
            st.markdown(f"*{selected} — CS lead/deficit vs. lane opponent*")
            fig = px.bar(
                champ_cs_diff, x="minute", y="avg_diff",
                labels={"avg_diff": "CS diff", "minute": "Minute"},
            )
            fig.update_layout(template=PLOT_TEMPLATE)
            st.plotly_chart(fig, use_container_width=True)

        champ_obj = st.session_state.objectives_cache.get(selected)
        if champ_obj is not None and not champ_obj.empty:
            st.markdown(f"*{selected} — objective participation (% of games)*")
            obj_pct = objective_participation_rates(champ_obj)
            oc1, oc2, oc3, oc4 = st.columns(4)
            oc1.metric("Dragons", f"{obj_pct['dragons']}%")
            oc2.metric("Barons", f"{obj_pct['barons']}%")
            oc3.metric("Heralds", f"{obj_pct['heralds']}%")
            oc4.metric("Towers", f"{obj_pct['towers']}%")

        champ_heatmap = st.session_state.heatmap_cache.get(selected)
        if champ_heatmap is not None:
            st.markdown(f"*{selected} — death & kill locations*")
            render_position_heatmap(champ_heatmap, f"{selected} — Death & Kill Map")


# ==================== Page: Compare ====================
def page_compare():
    render_hero("Compare Champions", "Two champions, side by side")
    champs_played = sorted(filtered_df["champion"].dropna().unique())
    if len(champs_played) < 2:
        st.info("Need at least 2 different champions played in this queue filter to compare.")
        return

    col_pick_a, col_pick_b = st.columns(2)
    champ_a = col_pick_a.selectbox("Champion A", champs_played, index=0, key="cmp_a")
    default_b_idx = 1 if len(champs_played) > 1 else 0
    champ_b = col_pick_b.selectbox("Champion B", champs_played, index=default_b_idx, key="cmp_b")

    sub_a = filtered_df[filtered_df["champion"] == champ_a]
    sub_b = filtered_df[filtered_df["champion"] == champ_b]
    ga, wa, wra = overall_win_rate(sub_a)
    gb, wb, wrb = overall_win_rate(sub_b)
    avgs_a, avgs_b = averages(sub_a), averages(sub_b)

    col_a, col_b = st.columns(2)
    with col_a:
        with section_card("cmp-a", champ_a, icon="🅰️", featured=True):
            st.image(ddragon.champion_icon_url(champ_a, version), width=72)
            metric_grid([
                ("Games", ga), ("Win Rate", f"{wra}%"), ("Avg KDA", avgs_a["kda"]),
                ("Avg CS/min", avgs_a["cs_per_min"]),
                ("Avg Kill Participation", f"{avgs_a['kill_participation']}%"),
                ("Avg Damage Share", f"{avgs_a['damage_share']}%"),
            ], cols_per_row=2)
    with col_b:
        with section_card("cmp-b", champ_b, icon="🅱️", featured=True):
            st.image(ddragon.champion_icon_url(champ_b, version), width=72)
            metric_grid([
                ("Games", gb), ("Win Rate", f"{wrb}%"), ("Avg KDA", avgs_b["kda"]),
                ("Avg CS/min", avgs_b["cs_per_min"]),
                ("Avg Kill Participation", f"{avgs_b['kill_participation']}%"),
                ("Avg Damage Share", f"{avgs_b['damage_share']}%"),
            ], cols_per_row=2)

    with section_card("cmp-charts", "Head to Head Trends", icon="📊"):
        st.markdown("**KDA trend — overlaid**")
        trend_a = rolling_trend(sub_a, "kda", window=5)
        trend_b = rolling_trend(sub_b, "kda", window=5)
        fig = go.Figure()
        if not trend_a.empty:
            fig.add_trace(go.Scatter(
                x=trend_a["game_creation"], y=trend_a["kda_rolling"], mode="lines",
                name=champ_a, line=dict(color="#2DD4BF", width=3),
            ))
        if not trend_b.empty:
            fig.add_trace(go.Scatter(
                x=trend_b["game_creation"], y=trend_b["kda_rolling"], mode="lines",
                name=champ_b, line=dict(color="#FB7185", width=3),
            ))
        fig.update_layout(template=PLOT_TEMPLATE, title="5-game rolling KDA average")
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("**Win rate by patch — overlaid**")
        patch_a = win_rate_by_patch(sub_a, min_games=2)
        patch_b = win_rate_by_patch(sub_b, min_games=2)
        if patch_a.empty and patch_b.empty:
            st.caption("Not enough games on any single patch yet for either champion.")
        else:
            fig = go.Figure()
            if not patch_a.empty:
                fig.add_trace(go.Bar(x=patch_a["patch"], y=patch_a["win_rate"], name=champ_a, marker_color="#2DD4BF"))
            if not patch_b.empty:
                fig.add_trace(go.Bar(x=patch_b["patch"], y=patch_b["win_rate"], name=champ_b, marker_color="#FB7185"))
            fig.update_layout(template=PLOT_TEMPLATE, barmode="group", yaxis_title="Win rate (%)")
            st.plotly_chart(fig, use_container_width=True)


# ==================== Page: Roles ====================
def page_roles():
    # Rift-only: ARAM and Arena have no positions, so including them here
    # would put an "ARAM" bar next to Top/Jungle/Mid as though it were a
    # role you play.
    rift = rift_only(filtered_df)
    lane_games = rift[rift["role_label"].isin(LANE_ROLES)] if not rift.empty else rift

    if lane_games.empty:
        render_hero("Roles", "Where you play, and how it goes")
        st.info(
            "No Summoner's Rift games with a recorded position in this queue filter. "
            "ARAM and Arena don't have roles, so there's nothing to break down here."
        )
        return

    role_wr = win_rate_by_role(lane_games)
    top_role = role_wr.sort_values("games", ascending=False).iloc[0]
    best_role = role_wr.sort_values("win_rate", ascending=False).iloc[0]
    render_hero(
        "Roles", "Where you play, and how it goes",
        stats=[
            ("Most Played", f"{top_role['role_label']}"),
            ("Best Win Rate", f"{best_role['role_label']} · {best_role['win_rate']}%"),
            ("Roles Played", len(role_wr)),
        ],
    )

    with section_card(
        "roles-overview", "Win Rate by Role",
        "Summoner's Rift games only — ARAM and Arena have no positions.",
        icon="🛡️", featured=True,
    ):
        fig = px.bar(
            role_wr, x="role_label", y="win_rate", hover_data=["games", "wins"],
            labels={"win_rate": "Win rate (%)", "role_label": "Role"},
        )
        fig.update_layout(
            template=PLOT_TEMPLATE,
            xaxis=dict(categoryorder="array", categoryarray=list(role_wr["role_label"])),
        )
        st.plotly_chart(fig, use_container_width=True)
        percent_table(role_wr, hide_index=True)

    with section_card("roles-averages", "Averages by Role", icon="📊"):
        rows = []
        for role in role_wr["role_label"]:
            scoped = lane_games[lane_games["role_label"] == role]
            avgs = averages(scoped)
            vis = vision_summary(scoped)
            rows.append({
                "role": role,
                "games": len(scoped),
                "win_rate": round(scoped["win"].mean() * 100, 1),
                "KDA": avgs["kda"],
                "CS/min": avgs["cs_per_min"],
                "Vision": avgs["vision_score"],
                "kill_participation": avgs["kill_participation"],
                "damage_share": avgs["damage_share"],
                "Wards placed": vis["wards_placed"],
            })
        percent_table(pd.DataFrame(rows), hide_index=True)
        st.caption(
            "Compare like with like — CS/min and vision differ hugely by position, "
            "so these are only meaningful within a row, not across them."
        )

    with section_card("roles-drill", "Champions by Role", icon="🧙"):
        picked_role = st.selectbox("Role", list(role_wr["role_label"]))
        scoped = lane_games[lane_games["role_label"] == picked_role]
        champ_wr = win_rate_by(scoped, "champion")
        if champ_wr.empty:
            st.caption(f"No champions recorded as {picked_role}.")
        else:
            g, w, wr_role = overall_win_rate(scoped)
            metric_grid([
                ("Games", g), ("Win Rate", f"{wr_role}%"), ("Champions", len(champ_wr)),
            ], cols_per_row=3)
            champion_card_grid(
                champ_wr.sort_values("games", ascending=False),
                max_cards=len(champ_wr),
                roles={c: picked_role for c in champ_wr["champion"]},
            )
            percent_table(champ_wr.sort_values("games", ascending=False), hide_index=True)


# ==================== Page: Teammates ====================
def page_duo():
    render_hero("Teammates", "Who you climb best with")
    with section_card(
        "duo-table", "Bot Lane Duo Win Rates",
        "Bot-lane pairing heuristic (ADC+support on your team) — not exact party data.",
        icon="🤝",
    ):
        duo_wr = win_rate_by(filtered_df.dropna(subset=["duo_partner"]), "duo_partner", min_games=2)
        if duo_wr.empty:
            st.write("No duo data yet (need 2+ games with the same bot-lane partner).")
        else:
            percent_table(duo_wr, hide_index=True)

    with section_card(
        "teammate-synergy", "Full Teammate Synergy",
        "Any frequently-recurring teammate, any role — not just bot lane. Still a "
        "heuristic (Riot's API doesn't expose real premade-party data), but covers "
        "anyone you queue with a lot, not only an ADC+support pairing.",
        icon="🧑‍🤝‍🧑", featured=True,
    ):
        synergy_df = teammate_synergy(filtered_df, min_games=3)
        if synergy_df.empty:
            st.write("No recurring teammates yet (need 3+ games with the same person).")
        else:
            percent_table(synergy_df, hide_index=True)


# ==================== Page: Raw Data ====================
def page_raw():
    render_hero("Raw Data", "Unfiltered — every mode, every match loaded.")
    with section_card("raw-table", "Full Match History", icon="🗂️", featured=True):
        # `all_df`, not `df`: this page and the export are the only places
        # that should show non-core modes alongside the rest. Everything else
        # reads the core-only frame. "Raw" would be a lie otherwise.
        ordered = all_df.sort_values("game_creation", ascending=False)
        st.dataframe(ordered, use_container_width=True)

    with section_card(
        "raw-export", "Export",
        "Every computed column this dashboard tracks, one row per game — "
        "for your own spreadsheet/analysis work.",
        icon="📤",
    ):
        # A few columns hold Python tuples/lists (items, build, teammate ids).
        # Those serialize to CSV as their repr, which is ugly but lossless and
        # still parseable — better than silently dropping the columns.
        st.download_button(
            "Download match history (CSV)",
            data=ordered.to_csv(index=False).encode("utf-8"),
            file_name=f"{GAME_NAME}_{TAG_LINE}_match_history.csv",
            mime="text/csv",
        )
        st.caption(f"{len(ordered)} games · {len(ordered.columns)} columns.")


# ==================== Page: Other Modes ====================
# Everything deliberately kept out of the main stats: ARAM, Arena, Swiftplay,
# and whatever rotating mode is live. These are here so the games aren't
# invisible, not so they can be compared to ranked — the whole reason they're
# excluded is that the comparison is meaningless.
def page_other_modes():
    render_hero(
        "Other Modes",
        "ARAM, Arena, Swiftplay and rotating queues — kept out of your main stats",
    )

    if other_modes_df.empty:
        with section_card("om-empty", "Nothing here yet", icon="🎲"):
            st.info(
                "None of your loaded games are outside standard Summoner's Rift. "
                "ARAM, Arena, Swiftplay and rotating modes would show up here."
            )
        return

    summary = mode_summary(other_modes_df)
    with section_card(
        "om-summary", "By Mode",
        f"{len(other_modes_df)} games across {len(summary)} modes.",
        icon="🎲", featured=True,
    ):
        percent_table(summary, hide_index=True)
        st.caption(
            "These are excluded from every other page. Different maps, team "
            "sizes and gold curves mean a win rate here isn't comparable to a "
            "Summoner's Rift one — averaging them together would describe neither."
        )

    mode_names = list(summary["mode"])
    picked = st.selectbox("Mode", mode_names, key="other-mode-pick")
    modes = other_modes_df.get(
        "game_mode", pd.Series([None] * len(other_modes_df), index=other_modes_df.index)
    )
    labels = pd.Series(
        [mode_label(q, m) for q, m in zip(other_modes_df["queue_id"], modes)],
        index=other_modes_df.index,
    )
    sub = other_modes_df[labels == picked]

    with section_card("om-detail", picked, f"{len(sub)} games", icon="📊"):
        if sub.empty:
            st.caption("No games in this mode.")
        else:
            avg = averages(sub)
            wins = int(sub["win"].sum())
            # Metrics are chosen per mode rather than shown uniformly. CS/min
            # in Arena is noise (no minions, 2v2 rounds), and lane stats don't
            # exist in ARAM. Showing a column that can't mean anything is how
            # a dashboard teaches people to distrust it.
            tiles = [
                ("Games", len(sub)),
                ("Win Rate", f"{wins / len(sub) * 100:.0f}%"),
                ("Avg KDA", avg["kda"]),
                ("Avg Damage Share", f"{avg['damage_share']:.0f}%"),
            ]
            if not sub["queue_category"].isin(LANELESS_QUEUES).all():
                tiles.append(("Avg CS/min", avg["cs_per_min"]))
                tiles.append(("Avg Vision Score", avg["vision_score"]))
            metric_grid(tiles, cols_per_row=3)

            st.markdown("**Champions played**")
            champs = win_rate_by(sub, "champion", min_games=1)
            percent_table(
                champs.sort_values("games", ascending=False).head(15), hide_index=True
            )

    with section_card("om-recent", "Recent Games", icon="🕹️"):
        recent_games_feed(sub.sort_values("game_creation", ascending=False).head(10))

