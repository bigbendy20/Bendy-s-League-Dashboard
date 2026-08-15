"""
Profile resolution — "whose stats am I looking at?"

Pure functions over dicts, so the rules are testable without a browser, a
database or a network. The ordering in `resolve_active` is the part worth
pinning: each fallback exists for a specific reason, and swapping any two of
them produces a site that mostly works and is wrong in one annoying way.
"""
import profiles


BENDY = profiles.make_profile("p1", "Bendy", "NA1", email="bendy@example.com")
FRIEND = profiles.make_profile("p2", "Friend", "EUW", email="friend@example.com")
NO_EMAIL = profiles.make_profile("p3", "Lurker", "NA1")


class TestMakeProfile:
    def test_display_name_defaults_to_the_riot_name(self):
        assert profiles.make_profile("p", "Bendy", "NA1")["display_name"] == "Bendy"

    def test_explicit_display_name_wins(self):
        p = profiles.make_profile("p", "Bendy", "NA1", display_name="Bendy (jg)")
        assert p["display_name"] == "Bendy (jg)"

    def test_leading_hash_is_stripped_from_the_tag(self):
        """Players copy their id as `Name#TAG`, so the hash arrives attached
        to the tag about half the time."""
        assert profiles.make_profile("p", "Bendy", "#NA1")["tag_line"] == "NA1"

    def test_riot_id_reads_the_way_players_write_it(self):
        assert profiles.riot_id(BENDY) == "Bendy#NA1"

    def test_riot_id_of_nothing_is_empty_not_an_error(self):
        assert profiles.riot_id(None) == ""


class TestBootstrapFromEnv:
    ENV = {"RIOT_GAME_NAME": "Bendy", "RIOT_TAG_LINE": "#NA1",
           "PLATFORM_REGION": "na1", "CONTINENTAL_REGION": "americas"}

    def test_builds_a_single_profile(self):
        [p] = profiles.bootstrap_from_env(self.ENV)
        assert p["game_name"] == "Bendy" and p["tag_line"] == "NA1"

    def test_unconfigured_env_yields_nothing(self):
        """First run, before onboarding. Must be empty rather than a profile
        with blank names, which would look registered and never resolve."""
        assert profiles.bootstrap_from_env({}) == []
        assert profiles.bootstrap_from_env({"RIOT_GAME_NAME": "Bendy"}) == []

    def test_puuid_is_left_unset(self):
        """Resolution must not require a network call. The puuid needs an API
        request, so it's filled in after the first fetch rather than being a
        precondition for showing the page."""
        assert profiles.bootstrap_from_env(self.ENV)[0]["puuid"] is None

    def test_regions_default_sensibly(self):
        env = {"RIOT_GAME_NAME": "Bendy", "RIOT_TAG_LINE": "NA1"}
        p = profiles.bootstrap_from_env(env)[0]
        assert p["platform_region"] == "na1"
        assert p["continental_region"] == "americas"


class TestResolveActive:
    ALL = [BENDY, FRIEND, NO_EMAIL]

    def test_explicit_request_wins(self):
        """`?profile=…` links have to work, and a viewer looking at a
        friend's page must not be bounced back to their own on every rerun."""
        active = profiles.resolve_active(self.ALL, requested_puuid="p2",
                                         signed_in_email="bendy@example.com")
        assert active["puuid"] == "p2"

    def test_falls_back_to_your_own_profile(self):
        active = profiles.resolve_active(self.ALL, signed_in_email="bendy@example.com")
        assert active["puuid"] == "p1"

    def test_falls_back_to_the_first_profile(self):
        """A signed-out visitor should see a populated site, not an error."""
        assert profiles.resolve_active(self.ALL)["puuid"] == "p1"

    def test_unknown_puuid_falls_through_rather_than_failing(self):
        """Links outlive profiles. A stale bookmark should land somewhere
        sensible instead of erroring."""
        active = profiles.resolve_active(self.ALL, requested_puuid="deleted",
                                         signed_in_email="friend@example.com")
        assert active["puuid"] == "p2"

    def test_unknown_email_falls_through(self):
        active = profiles.resolve_active(self.ALL, signed_in_email="stranger@example.com")
        assert active["puuid"] == "p1"

    def test_no_profiles_gives_none(self):
        assert profiles.resolve_active([]) is None

    def test_email_match_ignores_case(self):
        """Identity providers are inconsistent about casing, and a mismatch
        here silently lands you on someone else's page.

        Deliberately matches the *second* profile. Matching the first would
        pass even with the comparison broken, because the first profile is
        also the fallback — the assertion has to be able to tell the two
        apart, which an earlier version of this test could not.
        """
        active = profiles.resolve_active(self.ALL, signed_in_email="Friend@Example.COM")
        assert active["puuid"] == "p2"

    def test_a_profile_without_an_email_is_never_matched(self):
        """Blank-matches-blank would hand a signed-out viewer someone's
        page as though it were their own."""
        assert profiles.find_by_email(self.ALL, "") is None
        assert profiles.find_by_email([NO_EMAIL], None) is None


class TestIsOwnProfile:
    def test_true_for_your_own(self):
        assert profiles.is_own_profile(BENDY, "bendy@example.com")

    def test_false_for_someone_elses(self):
        assert not profiles.is_own_profile(FRIEND, "bendy@example.com")

    def test_case_insensitive(self):
        assert profiles.is_own_profile(BENDY, "BENDY@EXAMPLE.COM")

    def test_signed_out_owns_nothing(self):
        """Otherwise a signed-out viewer would be offered Settings on a
        profile that isn't theirs."""
        assert not profiles.is_own_profile(BENDY, "")
        assert not profiles.is_own_profile(NO_EMAIL, "")


class TestClimbGoalIsPerProfile:
    """The goal belongs to the player, not to the deployment.

    It used to be `GOAL_TIER`/`GOAL_RANK` in `.env`: one value for the whole
    site, so every friend's page displayed Bendy's target as though it were
    theirs. On Streamlit Cloud it was also unsettable — `.env` isn't writable
    there and wouldn't survive a restart if it were.
    """

    def test_a_profile_carries_its_own_goal(self):
        p = profiles.make_profile("p1", "Bendy", "NA1", goal_tier="diamond",
                                  goal_rank="iv")
        assert p["goal_tier"] == "DIAMOND"
        assert p["goal_rank"] == "IV"

    def test_no_goal_is_none_not_an_empty_string(self):
        """`if not GOAL_TIER` decides whether the card renders at all, and an
        empty string from a blank form field must read the same as never
        having set one."""
        p = profiles.make_profile("p1", "Bendy", "NA1")
        assert p["goal_tier"] is None
        assert profiles.make_profile("p2", "X", "NA1", goal_tier="  ")["goal_tier"] is None

    def test_two_profiles_hold_different_goals(self):
        """The distinguishing case: with one shared setting this passes only
        by accident, because both would read back whatever was written last."""
        a = profiles.make_profile("p1", "Bendy", "NA1", goal_tier="MASTER")
        b = profiles.make_profile("p2", "Friend", "NA1", goal_tier="SILVER",
                                  goal_rank="II")
        assert (a["goal_tier"], b["goal_tier"]) == ("MASTER", "SILVER")

    def test_the_goal_fields_are_part_of_the_record(self):
        """`PROFILE_FIELDS` drives the SQL schema, the upsert and the row
        decoder. A field missing from it saves and then reads back absent."""
        assert "goal_tier" in profiles.PROFILE_FIELDS
        assert "goal_rank" in profiles.PROFILE_FIELDS
