"""
Local-time conversion, Wilson confidence margins, and the tip window.

The timezone half of this exists because every time-based stat in the app was
silently reported in UTC: `pd.to_datetime(ms, unit="ms")` produces a naive
timestamp that *looks* local and isn't. A 9pm Thursday game in New York was
filed as Friday, which is why "Fridays are your best day" was partly Thursday
evenings. Nothing failed loudly — the numbers were just wrong.
"""
import datetime
import zoneinfo

import pandas as pd

import ddragon
import insights
import stats
from conftest import make_match

NY = zoneinfo.ZoneInfo("America/New_York")
TOKYO = zoneinfo.ZoneInfo("Asia/Tokyo")


def _ms(y, m, d, h, minute=0):
    """UTC wall-clock -> epoch milliseconds, the way Riot sends it."""
    return int(datetime.datetime(y, m, d, h, minute,
                                 tzinfo=datetime.timezone.utc).timestamp() * 1000)


class TestLocalTime:
    def test_converts_utc_to_local(self):
        # 20:00 UTC in January is 15:00 EST.
        assert stats.to_local_time(_ms(2026, 1, 15, 20), NY).hour == 15

    def test_respects_dst(self):
        """The whole reason this uses fromtimestamp() rather than a fixed offset.

        January is EST (UTC-5), July is EDT (UTC-4). A single cached offset
        would put one of these an hour out — enough to move games between
        hour buckets, and across midnight, between *days*.
        """
        winter = stats.to_local_time(_ms(2026, 1, 15, 20), NY)
        summer = stats.to_local_time(_ms(2026, 7, 15, 20), NY)
        assert (winter.hour, summer.hour) == (15, 16)

    def test_weekday_can_shift_across_midnight(self):
        """The actual reported bug: a late-evening game filed as the next day.

        01:00 UTC Friday is 20:00 Thursday in New York.
        """
        utc_friday = _ms(2026, 8, 14, 1)  # Friday 01:00 UTC
        assert stats.to_local_time(utc_friday, NY).day_name() == "Thursday"
        # ...and the reverse direction, to prove it's a real conversion and
        # not a constant subtraction that happens to fit the first case.
        assert stats.to_local_time(utc_friday, TOKYO).day_name() == "Friday"

    def test_result_is_naive(self):
        """Downstream code sorts and compares these against naive timestamps."""
        assert stats.to_local_time(_ms(2026, 1, 15, 20), NY).tzinfo is None

    def test_parse_match_uses_local_time(self):
        """Guards the wiring, not just the helper — the bug was that the
        helper didn't exist and parse_match called pd.to_datetime directly."""
        match = make_match(creation_ms=_ms(2026, 1, 15, 20))
        row = stats.parse_match(match, "me-puuid")
        local = datetime.datetime.fromtimestamp(_ms(2026, 1, 15, 20) / 1000)
        assert row["game_creation"] == pd.Timestamp(local)


class TestWilsonMargin:
    def test_shrinks_as_sample_grows(self):
        small = stats.wilson_margin(6, 10)
        large = stats.wilson_margin(600, 1000)
        assert small > large
        assert large < 5 and small > 20

    def test_realistic_weekday_bucket_is_wide(self):
        """38 games is a typical day-of-week sample. If this ever narrows to
        something confident-looking, the math is wrong."""
        assert 12 < stats.wilson_margin(24, 38) < 18

    def test_no_games_gives_no_confidence(self):
        assert stats.wilson_margin(0, 0) == 100.0

    def test_handles_unanimous_results(self):
        """The normal approximation returns 0 for 5/5 — a claim of perfect
        certainty from five games. Wilson must not."""
        assert stats.wilson_margin(5, 5) > 10
        assert stats.wilson_margin(0, 5) > 10


class TestSeparated:
    def test_clearly_different_rates_separate(self):
        assert stats.separated(90, 100, 20, 100)

    def test_small_samples_do_not_separate(self):
        """63% over 38 vs 44% over 32 — a real gap in the user's own data
        that is nonetheless well inside the noise."""
        assert not stats.separated(24, 38, 14, 32)

    def test_empty_bucket_never_separates(self):
        assert not stats.separated(0, 0, 50, 100)


def _weekday_frame(spec):
    """Build games landing on chosen weekdays. `spec` maps weekday index
    (0 = Monday) to (games, wins). 2026-01-05 is a Monday.
    """
    monday = datetime.datetime(2026, 1, 5, 12)
    rows = []
    for weekday, (games, wins) in spec.items():
        for i in range(games):
            rows.append({
                "game_creation": pd.Timestamp(
                    monday + datetime.timedelta(days=weekday + 7 * i)
                ),
                "win": i < wins,
                "champion": "Ahri",
                "queue_category": "Ranked",
                "patch": "15.1",
            })
    return pd.DataFrame(rows)


def _weekday_tip(frame):
    tips = [t for t in insights.generate_recommendations(frame) if "best day" in t["text"]]
    assert tips, "expected a day-of-week tip from this frame"
    return tips[0]


class TestTipMetadata:
    def test_every_tip_carries_its_sample(self):
        tips = insights.generate_recommendations(_weekday_frame({0: (12, 8), 2: (12, 5)}))
        assert tips
        for tip in tips:
            assert tip["games"] > 0
            assert tip["margin"] > 0
            assert isinstance(tip["weak"], bool)

    def test_moderate_gap_on_small_samples_is_weak(self):
        """67% over 12 games vs 42% over 12 clears the 15-point display
        threshold but sits well inside both margins — exactly the case the
        card used to state as fact.

        An earlier version of this test used a frame that produced no
        day-of-week tip at all, so it passed without ever exercising the flag.
        `_weekday_tip` asserts the tip exists precisely to stop that.
        """
        tip = _weekday_tip(_weekday_frame({0: (12, 8), 2: (12, 5)}))
        assert tip["weak"] is True
        assert tip["margin"] > 15

    def test_large_gap_on_large_samples_is_not_weak(self):
        """The other side of the boundary — without this, `weak = True`
        everywhere would pass the test above."""
        tip = _weekday_tip(_weekday_frame({0: (100, 80), 2: (100, 20)}))
        assert tip["weak"] is False
        assert tip["margin"] < 10

    def test_below_minimum_returns_nothing(self):
        assert insights.generate_recommendations(_weekday_frame({0: (3, 2)})) == []


class TestMultipleComparisons:
    """Scanning many buckets and reporting the winner inflates it.

    Without a correction, the "best hour" tip on the user's own data claimed
    83% vs 20% off six games and was not flagged — a number that exists
    because 24 buckets were searched, not because that hour is good.
    """

    def test_z_widens_with_more_buckets(self):
        assert stats.z_for(1) == pytest_approx(1.96)
        assert stats.z_for(7) > stats.z_for(1)
        assert stats.z_for(24) > stats.z_for(7)

    def test_correction_suppresses_a_scanned_extreme(self):
        """6-1 vs 1-5 clears the uncorrected bar and fails the corrected one."""
        assert stats.separated(6, 7, 1, 6, comparisons=1)
        assert not stats.separated(6, 7, 1, 6, comparisons=24)

    def test_correction_does_not_erase_real_effects(self):
        """A genuinely large sample survives the correction — otherwise this
        would just be a switch that turns every tip grey."""
        assert stats.separated(80, 100, 20, 100, comparisons=24)

    @staticmethod
    def _hour_frame(peak, trough):
        """Twelve hour-buckets, with a chosen peak and trough.

        The bucket count matters: the correction scales with how many hours
        were searched, so a two-bucket frame has no selection effect to
        correct and belongs in a different test.
        """
        base = datetime.datetime(2026, 1, 5)
        spec = {20: peak, 11: trough}
        spec.update({h: (12, 6) for h in range(12, 22) if h not in spec})
        rows = []
        for hour, (games, wins) in spec.items():
            for i in range(games):
                rows.append({
                    "game_creation": pd.Timestamp(base + datetime.timedelta(days=i, hours=hour)),
                    "win": i < wins, "champion": "Ahri",
                    "queue_category": "Ranked", "patch": "15.1",
                })
        tips = [t for t in insights.generate_recommendations(pd.DataFrame(rows))
                if "starting around" in t["text"]]
        assert tips, "expected an hour-of-day tip from this frame"
        return tips[0]

    def test_hour_tip_on_thin_buckets_is_weak(self):
        """71% vs 38% on ~13 games each: a 33-point gap that looks striking
        and doesn't survive being the best of twelve buckets."""
        assert self._hour_frame((14, 10), (13, 5))["weak"] is True

    def test_hour_tip_on_deep_buckets_is_firm(self):
        """The same shape of pattern with real depth behind it must still be
        stated plainly — otherwise the correction is just a mute button."""
        assert self._hour_frame((60, 45), (60, 20))["weak"] is False


def pytest_approx(value, tol=0.01):
    class _Approx:
        def __eq__(self, other):
            return abs(other - value) < tol
    return _Approx()


class TestRecentWindow:
    """The games-window control. Previously inlined in a Streamlit renderer,
    where deleting it outright broke nothing any test could see."""

    def _frame(self, n):
        base = datetime.datetime(2026, 1, 5)
        return pd.DataFrame([{
            "game_creation": pd.Timestamp(base + datetime.timedelta(days=i)),
            "win": i % 2 == 0,
        } for i in range(n)])

    def test_trims_to_the_most_recent_games(self):
        out = stats.recent_window(self._frame(100), 10)
        assert len(out) == 10
        assert out["game_creation"].min() > self._frame(100)["game_creation"].iloc[89 - 1]

    def test_none_means_everything(self):
        assert len(stats.recent_window(self._frame(100), None)) == 100

    def test_window_larger_than_history_is_a_no_op(self):
        assert len(stats.recent_window(self._frame(20), 50)) == 20

    def test_takes_the_tail_not_the_head(self):
        """A `.head()` slip would still return the right row count."""
        frame = self._frame(50)
        out = stats.recent_window(frame, 5)
        assert out["game_creation"].iloc[-1] == frame["game_creation"].iloc[-1]

    def test_window_changes_what_the_tips_see(self):
        """End to end: the same history, two windows, different conclusions.

        Recent games are all losses; the full history is balanced.
        """
        base = datetime.datetime(2026, 1, 5)
        rows = [{"game_creation": pd.Timestamp(base + datetime.timedelta(days=i)),
                 "win": i < 40, "champion": "Ahri",
                 "queue_category": "Ranked", "patch": "15.1"} for i in range(60)]
        frame = pd.DataFrame(rows)
        all_tips = " ".join(t["text"] for t in insights.generate_recommendations(frame))
        recent = " ".join(t["text"] for t in
                          insights.generate_recommendations(stats.recent_window(frame, 15)))
        assert all_tips != recent


class TestScanFloors:
    def test_buckets_below_the_scan_floor_produce_no_tip(self):
        """Five games per weekday used to be enough to crown a 'best day'."""
        thin = _weekday_frame({0: (8, 7), 2: (8, 1)})
        assert not [t for t in insights.generate_recommendations(thin)
                    if "best day" in t["text"]]

    def test_correction_uses_the_full_search_space(self):
        """Ten tiny buckets get filtered out before the comparison, but the
        peak was still chosen from among them. Correcting by the two
        survivors instead of all twelve understates the selection effect —
        which is exactly how a six-game 'best hour' got stated as fact.
        """
        # 77% vs 23% on 13 games each. Chosen because it lands *between* the
        # two corrections: firm if you only count the 2 surviving buckets,
        # weak once you count all 12 the peak was picked from. A less
        # borderline case passes either way and proves nothing.
        base = datetime.datetime(2026, 1, 5)
        spec = {20: (13, 10), 11: (13, 3)}
        spec.update({h: (6, 3) for h in range(12, 22) if h not in spec})
        rows = []
        for hour, (games, wins) in spec.items():
            for i in range(games):
                rows.append({
                    "game_creation": pd.Timestamp(base + datetime.timedelta(days=i, hours=hour)),
                    "win": i < wins, "champion": "Ahri",
                    "queue_category": "Ranked", "patch": "15.1",
                })
        tips = [t for t in insights.generate_recommendations(pd.DataFrame(rows))
                if "starting around" in t["text"]]
        assert tips, "expected an hour-of-day tip"
        assert tips[0]["weak"] is True


class TestHourLabel:
    def test_midnight_and_noon(self):
        """The two everyone gets wrong: hour 0 is 12 AM, hour 12 is 12 PM."""
        assert stats.hour_label(0) == "12 AM"
        assert stats.hour_label(12) == "12 PM"

    def test_morning_and_evening(self):
        assert stats.hour_label(9) == "9 AM"
        assert stats.hour_label(13) == "1 PM"
        assert stats.hour_label(23) == "11 PM"

    def test_covers_every_hour_without_duplicates(self):
        labels = [stats.hour_label(h) for h in range(24)]
        assert len(set(labels)) == 24

    def test_tip_text_uses_12_hour_format(self):
        """Through insights, not just the helper."""
        tip = TestMultipleComparisons._hour_frame((60, 45), (60, 20))
        assert "PM" in tip["text"] or "AM" in tip["text"]
        assert ":00" not in tip["text"]


class TestMostPlayedChampion:
    """Drives the site background art."""

    def test_picks_the_most_frequent(self):
        df = pd.DataFrame({"champion": ["Ahri", "Ahri", "Ahri", "Zed", "Zed"]})
        assert stats.most_played_champion(df) == "Ahri"

    def test_ties_break_alphabetically(self):
        """Stability matters more than which one wins: a background that
        flips between two equally-played champions every refresh reads as a
        bug. `value_counts` order is not guaranteed for ties."""
        first = stats.most_played_champion(pd.DataFrame({"champion": ["Zed", "Ahri"]}))
        second = stats.most_played_champion(pd.DataFrame({"champion": ["Ahri", "Zed"]}))
        assert first == second == "Ahri"

    def test_empty_frame_returns_none(self):
        """None is the signal to fall back — must not raise or return NaN."""
        assert stats.most_played_champion(pd.DataFrame()) is None

    def test_missing_column_returns_none(self):
        assert stats.most_played_champion(pd.DataFrame({"win": [True]})) is None

    def test_all_null_champions_returns_none(self):
        df = pd.DataFrame({"champion": [None, None]})
        assert stats.most_played_champion(df) is None

    def test_ignores_nulls_when_counting(self):
        df = pd.DataFrame({"champion": ["Zed", None, None, "Ahri", "Ahri"]})
        assert stats.most_played_champion(df) == "Ahri"


class TestHeroIcons:
    """Which image the circle at the top of the page shows.

    Both of these were conditional expressions buried in app.py and layout.py.
    Mutating either — always taking the fallback, or showing the profile icon
    on a champion page — passed the entire suite, because nothing could reach
    the code. Extracting them into ddragon is what made them testable.
    """
    V = "15.1.1"

    def test_profile_icon_wins_when_riot_gave_one(self):
        url = ddragon.site_icon_url(4568, "Ahri", self.V)
        assert "profileicon/4568" in url

    def test_falls_back_to_champion_when_id_missing(self):
        url = ddragon.site_icon_url(None, "Ahri", self.V)
        assert "champion/Ahri" in url

    def test_icon_id_zero_is_a_real_icon(self):
        """0 is the default League icon, not 'absent'. A truthiness check
        instead of `is None` would throw it away."""
        assert "profileicon/0" in ddragon.site_icon_url(0, "Ahri", self.V)

    def test_champion_page_shows_that_champion(self):
        site = "https://example.invalid/me.png"
        assert "champion/Zed" in ddragon.hero_icon_url("Zed", site, self.V)

    def test_site_page_shows_you(self):
        site = "https://example.invalid/me.png"
        assert ddragon.hero_icon_url(None, site, self.V) == site


class TestSplitRecord:
    """Shared complement-baseline helper.

    Written twice originally — once in live_tips (correct) and once inline in
    insights (biased). Unified so the comparison can't diverge again.
    """

    FRAME = pd.DataFrame({
        "champion": ["Shaco"] * 3 + ["Ahri"] * 7,
        "win": [True] * 3 + [False] * 7,
    })

    def test_returns_disjoint_halves(self):
        inside, outside = stats.split_record(self.FRAME, self.FRAME["champion"] == "Shaco")
        assert inside == (3, 3)
        assert outside == (7, 0)

    def test_halves_sum_to_the_whole(self):
        inside, outside = stats.split_record(self.FRAME, self.FRAME["win"])
        assert inside[0] + outside[0] == len(self.FRAME)

    def test_empty_frame_is_handled(self):
        assert stats.split_record(pd.DataFrame(), []) == ((0, 0), (0, 0))

    def test_superset_baseline_would_hide_a_real_edge(self):
        """The bug this exists to prevent, using the real numbers: 108/188
        against the other 411 games is significant; against all 599 — which
        include those same 188 — it isn't."""
        assert stats.separated(108, 188, 190, 411)
        assert not stats.separated(108, 188, 298, 599)


class TestRecentFormBaseline:
    """Recent form must be compared against the games *before* it.

    Comparing the last 10 games to the whole history includes those same 10
    in the baseline, which dilutes the very swing the tip is trying to
    report. Reverting this comparison used to pass the entire suite — nothing
    exercised the flag.

    The fixture is chosen to sit between the two: 20% over the last 10
    against 60% over the prior 30 is significant (z = 2.19); against all 40
    games, which include those 10, it isn't (z = 1.71).
    """

    @staticmethod
    def _frame():
        base = datetime.datetime(2026, 1, 5, 12)
        rows = []
        # 30 prior games at 60%, then 10 recent at 20%.
        for i in range(30):
            rows.append((base + datetime.timedelta(hours=i), i < 18))
        for i in range(10):
            rows.append((base + datetime.timedelta(hours=30 + i), i < 2))
        return pd.DataFrame([{
            "game_creation": pd.Timestamp(t), "win": w,
            "champion": "Ahri", "queue_category": "Ranked", "patch": "15.1",
        } for t, w in rows])

    def _recent_tip(self):
        tips = [t for t in insights.generate_recommendations(self._frame())
                if "last 10 games" in t["text"]]
        assert tips, "expected a recent-form tip"
        return tips[0]

    def test_recent_slump_is_reported(self):
        assert "20%" in self._recent_tip()["text"]

    def test_slump_is_not_dismissed_as_noise(self):
        """With the complement baseline this clears significance. With the
        superset baseline it doesn't, and the tip would be greyed out."""
        assert self._recent_tip()["weak"] is False
