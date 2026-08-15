"""
Which games count as "your stats", and role-split matchups.

The scoping rule is an allow-list: standard Summoner's Rift only (normal
draft/blind/quickplay and ranked). Everything else — ARAM, Arena, Swiftplay,
rotating modes — is split off. The tests below lean hard on the *unknown
queue* case, because that's the one that decides whether a mode Riot ships
next month quietly contaminates a CS/min average or lands in Other Modes.
"""
import datetime

import pandas as pd

import stats


def _games(spec):
    """spec: list of (queue_id, win). Champion/role filled in for grouping."""
    base = datetime.datetime(2026, 1, 5, 12)
    return pd.DataFrame([{
        "queue_id": qid,
        "queue_category": stats.queue_category(qid),
        "game_mode": "CLASSIC",
        "game_creation": pd.Timestamp(base + datetime.timedelta(hours=i)),
        "win": win,
        "champion": "Ahri",
        "role_label": "Mid",
    } for i, (qid, win) in enumerate(spec)])


class TestCoreQueues:
    def test_ranked_and_normal_sr_are_core(self):
        for qid in (400, 420, 430, 440, 490):
            assert stats.is_core_queue(qid), f"queue {qid} should count"

    def test_other_modes_are_not_core(self):
        for qid in (450, 480, 700, 900, 1700, 1750, 1900):
            assert not stats.is_core_queue(qid), f"queue {qid} should be excluded"

    def test_unknown_queue_is_excluded_by_default(self):
        """The direction that matters. Riot adds rotating modes constantly;
        an unmapped id must not silently join your ranked averages."""
        assert not stats.is_core_queue(99999)
        assert not stats.is_core_queue(None)

    def test_core_and_non_core_partition_the_data(self):
        """Every game lands in exactly one bucket. If these ever stop summing
        to the total, some mode has vanished from the app entirely."""
        df = _games([(420, True), (450, False), (1700, True), (99999, False), (400, True)])
        core = stats.core_only(df)
        other = stats.non_core_only(df)
        assert len(core) + len(other) == len(df)
        assert set(core.index).isdisjoint(other.index)

    def test_core_only_keeps_the_right_games(self):
        df = _games([(420, True), (450, True), (480, True), (400, True)])
        assert sorted(stats.core_only(df)["queue_id"]) == [400, 420]

    def test_aram_cannot_move_a_core_win_rate(self):
        """The actual point of the feature, stated as a number: five ARAM
        losses must not drag a 100% ranked record down."""
        df = _games([(420, True), (420, True), (420, True)]
                    + [(450, False)] * 5)
        assert stats.core_only(df)["win"].mean() == 1.0

    def test_empty_frame_is_handled(self):
        empty = pd.DataFrame()
        assert stats.core_only(empty).empty
        assert stats.non_core_only(empty).empty

    def test_frame_without_queue_id_does_not_raise(self):
        """Some internal frames are built column-by-column; scoping them
        should degrade rather than explode."""
        df = pd.DataFrame({"win": [True]})
        assert len(stats.core_only(df)) == 1
        assert stats.non_core_only(df).empty


class TestModeLabels:
    def test_known_queue_uses_the_curated_name(self):
        assert stats.mode_label(450, "ARAM") == "ARAM"
        assert stats.mode_label(1700, "CHERRY") == "Arena"

    def test_unknown_queue_falls_back_to_game_mode(self):
        """"Other (880)" tells you nothing; Riot's own gameMode at least
        says what it was."""
        label = stats.mode_label(880, "SWIFTPLAY")
        assert "Swiftplay" in label and "880" in label

    def test_unknown_queue_without_mode_still_labels(self):
        assert "9999" in stats.mode_label(9999, None)

    def test_mode_summary_groups_and_counts(self):
        df = _games([(450, True), (450, False), (1700, True)])
        summary = stats.mode_summary(df)
        aram = summary[summary["mode"] == "ARAM"].iloc[0]
        assert aram["games"] == 2 and aram["wins"] == 1
        assert aram["win_rate"] == 50.0

    def test_mode_summary_on_empty_returns_shaped_frame(self):
        out = stats.mode_summary(pd.DataFrame())
        assert list(out.columns) == ["mode", "games", "wins", "win_rate"]


def _matchup_games(rows):
    """rows: list of (role, opponent, win)."""
    base = datetime.datetime(2026, 1, 5, 12)
    return pd.DataFrame([{
        "queue_id": 420,
        "queue_category": "Ranked",
        "game_creation": pd.Timestamp(base + datetime.timedelta(hours=i)),
        "champion": "Shaco",
        "role_label": role,
        "opponent_champion": opp,
        "win": win,
    } for i, (role, opp, win) in enumerate(rows)])


class TestMatchupsByRole:
    """A champion played in two positions faces two different opponent pools.

    Real case from the data this was built against: Shaco appears in Jungle
    (187 games), Top (5), Mid (6) and Support (6). Pooling those produced a
    matchup table describing no actual matchup.
    """

    FRAME = _matchup_games([
        ("Jungle", "Graves", True), ("Jungle", "Graves", True),
        ("Jungle", "Graves", False),
        ("Top", "Graves", False), ("Top", "Graves", False),
        ("Top", "Darius", True),
    ])

    def test_role_filter_changes_the_answer(self):
        jungle = stats.matchup_win_rate(self.FRAME, "Shaco", role="Jungle")
        top = stats.matchup_win_rate(self.FRAME, "Shaco", role="Top")
        jg_graves = jungle[jungle["vs_champion"] == "Graves"].iloc[0]
        top_graves = top[top["vs_champion"] == "Graves"].iloc[0]
        assert jg_graves["win_rate"] > 60 and top_graves["win_rate"] == 0.0

    def test_pooled_result_matches_neither_role(self):
        """The bug, as an assertion: the combined number sits between the two
        and describes neither."""
        pooled = stats.matchup_win_rate(self.FRAME, "Shaco")
        row = pooled[pooled["vs_champion"] == "Graves"].iloc[0]
        assert row["games"] == 5
        assert 0 < row["win_rate"] < 66.7

    def test_no_role_filter_keeps_every_game(self):
        pooled = stats.matchup_win_rate(self.FRAME, "Shaco")
        assert pooled["games"].sum() == len(self.FRAME)

    def test_unplayed_role_returns_empty(self):
        assert stats.matchup_win_rate(self.FRAME, "Shaco", role="Support").empty

    def test_roles_played_is_in_lane_order(self):
        """Ordered Top -> Support, not by frequency: a control that
        reshuffles as you play is worse than one that's slightly arbitrary."""
        assert stats.roles_played(self.FRAME, "Shaco") == ["Top", "Jungle"]

    def test_roles_played_keeps_unexpected_labels(self):
        odd = _matchup_games([("Jungle", "Graves", True), ("ARAM", "Sona", True)])
        assert set(stats.roles_played(odd, "Shaco")) == {"Jungle", "ARAM"}

    def test_by_role_table_tags_every_row(self):
        table = stats.matchup_win_rate_by_role(self.FRAME, "Shaco")
        assert set(table["role"]) == {"Top", "Jungle"}
        assert table["games"].sum() == len(self.FRAME)

    def test_by_role_on_unknown_champion_is_shaped_not_crashing(self):
        out = stats.matchup_win_rate_by_role(self.FRAME, "Teemo")
        assert list(out.columns) == ["role", "vs_champion", "games", "wins", "win_rate"]
        assert out.empty


class TestRoleScope:
    """Scoping a frame to one role — the Deep-Dive's page-level filter.

    Runes, summoner spells and builds are all role-dependent in the same way
    matchups are. Pooling them mostly measures which role you play most: in
    the history this was built against, the two best-looking summoner spell
    combos (Flash+Smite 53% over 209 games, Smite+Ignite 57% over 188) are
    both jungle-only, because Smite is a positional requirement rather than a
    good choice. Scoped to Jungle, comparing them finally means something.
    """

    FRAME = pd.DataFrame({
        "role_label": ["Jungle"] * 6 + ["Mid"] * 4,
        "champion": ["Shaco"] * 10,
        "win": [True] * 5 + [False] + [False] * 4,
        "summoner_combo": [(4, 11)] * 6 + [(4, 14)] * 4,
        "queue_id": [420] * 10,
    })

    def test_none_returns_everything(self):
        assert len(stats.role_scope(self.FRAME, None)) == 10

    def test_scoping_narrows_to_the_role(self):
        jungle = stats.role_scope(self.FRAME, "Jungle")
        assert len(jungle) == 6
        assert set(jungle["role_label"]) == {"Jungle"}

    def test_scoping_changes_the_win_rate(self):
        """The point of the feature, as a number: 83% in jungle, 0% in mid,
        50% pooled — a figure that describes neither role."""
        assert stats.role_scope(self.FRAME, "Jungle")["win"].mean() == 5 / 6
        assert stats.role_scope(self.FRAME, "Mid")["win"].mean() == 0.0
        assert stats.role_scope(self.FRAME, None)["win"].mean() == 0.5

    def test_spell_combos_separate_by_role(self):
        """Smite appears in every jungle game and no mid game, so pooled
        spell stats are really a picture of what you play."""
        jungle = stats.win_rate_by(stats.role_scope(self.FRAME, "Jungle"), "summoner_combo")
        assert list(jungle["summoner_combo"]) == [(4, 11)]

    def test_unplayed_role_gives_an_empty_frame(self):
        assert stats.role_scope(self.FRAME, "Support").empty

    def test_frame_without_roles_is_returned_unchanged(self):
        plain = pd.DataFrame({"win": [True, False]})
        assert len(stats.role_scope(plain, "Jungle")) == 2

    def test_empty_frame_is_handled(self):
        assert stats.role_scope(pd.DataFrame(), "Jungle").empty


class TestUnseenMatchIds:
    """The auto-refresh poll's decision function.

    Kept out of riot_client so it's testable without a network client. The
    whole point of the poll is that it's one API call instead of fourteen —
    which only pays off if it correctly answers "is there anything new?".
    """

    KNOWN = ("NA1_3", "NA1_2", "NA1_1")

    def test_nothing_new_returns_empty(self):
        assert stats.unseen_match_ids(list(self.KNOWN), self.KNOWN) == []

    def test_new_games_are_detected(self):
        latest = ["NA1_5", "NA1_4", "NA1_3", "NA1_2", "NA1_1"]
        assert stats.unseen_match_ids(latest, self.KNOWN) == ["NA1_5", "NA1_4"]

    def test_newest_first_order_is_preserved(self):
        """So the caller can say '2 new games' and mean it."""
        latest = ["NA1_9", "NA1_8", "NA1_1"]
        assert stats.unseen_match_ids(latest, self.KNOWN)[0] == "NA1_9"

    def test_failed_call_is_treated_as_nothing_new(self):
        """`None` means the API call failed — most likely an expired dev key,
        since they last 24 hours. A failed poll must be quiet, not disruptive,
        and above all must not look like a full set of new games."""
        assert stats.unseen_match_ids(None, self.KNOWN) == []

    def test_empty_response_is_not_new_games(self):
        assert stats.unseen_match_ids([], self.KNOWN) == []

    def test_empty_baseline_reports_everything(self):
        """First load, before anything is known. This is also why the
        baseline is derived from loaded data rather than set inside the fetch
        — an accidentally-empty baseline makes every poll trigger a refresh."""
        assert stats.unseen_match_ids(["NA1_1"], ()) == ["NA1_1"]

    def test_none_baseline_does_not_raise(self):
        assert stats.unseen_match_ids(["NA1_1"], None) == ["NA1_1"]
