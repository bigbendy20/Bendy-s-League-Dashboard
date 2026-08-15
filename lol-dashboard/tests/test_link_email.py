"""
Linking a sign-in email to a League profile.

Two identities have to meet: the address someone authenticates with, and the
Riot account whose games they want to see. `allowed_emails` decides who gets
in; this decides whose page they land on and whose climb goal they can edit.

The tests lean on the failure directions, because every plausible bug here
sends a real person to someone else's page and nothing errors when it does.
"""
import sqlite3

import link_email
import profiles
import store


def _store(*people):
    s = store.SqlStore(sqlite3.connect(":memory:"), paramstyle="?")
    for name, tag, email in people:
        # puuid includes the tag: two players *can* share a game name, and
        # keying on the name alone made the second upsert overwrite the first
        # — so the ambiguity test had only one profile to be ambiguous about.
        s.upsert_profile(profiles.make_profile(
            f"puuid-{name}-{tag}", name, tag, email=email))
    return s


class TestLinking:
    def test_an_email_is_attached_to_the_named_profile(self):
        s = _store(("Bendy", "NA1", None), ("Friend", "KR", None))
        link_email.link(s, "Friend#KR", "friend@example.com")
        by_name = {p["game_name"]: p for p in s.list_profiles()}
        assert by_name["Friend"]["email"] == "friend@example.com"
        assert by_name["Bendy"]["email"] is None, "the wrong profile was touched"

    def test_the_address_is_normalised(self):
        """`auth.is_allowed` and `resolve_active` both compare lowercased and
        trimmed. Storing a capitalised address would let someone in and then
        fail to find their profile — in for the site, absent from their own
        page."""
        s = _store(("Bendy", "NA1", None))
        link_email.link(s, "Bendy#NA1", "  Bendy@Example.COM ")
        assert s.list_profiles()[0]["email"] == "bendy@example.com"

    def test_a_bare_name_works_when_it_is_unambiguous(self):
        """Nobody types a tag from memory."""
        s = _store(("Bendy", "NA1", None))
        link_email.link(s, "Bendy", "bendy@example.com")
        assert s.list_profiles()[0]["email"] == "bendy@example.com"

    def test_a_bare_name_is_refused_when_it_is_ambiguous(self):
        """Two people can share a game name on different tags. Picking one
        silently would link the wrong account and look like it worked."""
        s = _store(("Bendy", "NA1", None), ("Bendy", "EUW", None))
        try:
            link_email.link(s, "Bendy", "bendy@example.com")
        except ValueError as exc:
            assert "more than one" in str(exc)
        else:
            raise AssertionError("an ambiguous name was accepted")

    def test_an_unknown_riot_id_is_an_error_naming_what_exists(self):
        s = _store(("Bendy", "NA1", None))
        try:
            link_email.link(s, "Nobody#NA1", "x@example.com")
        except LookupError as exc:
            assert "Bendy#NA1" in str(exc), "the error should list real options"
        else:
            raise AssertionError("an unknown profile was accepted")

    def test_one_address_cannot_belong_to_two_profiles(self):
        """The most important guard here. `resolve_active` returns the *first*
        profile whose email matches, so a duplicate doesn't raise anywhere —
        it quietly sends one person to the other's page, permanently, and
        looks like a bug in the site rather than in the data."""
        s = _store(("Bendy", "NA1", "shared@example.com"), ("Friend", "KR", None))
        try:
            link_email.link(s, "Friend#KR", "SHARED@example.com")
        except ValueError as exc:
            assert "already linked" in str(exc)
        else:
            raise AssertionError("an address was linked to two profiles")

    def test_relinking_the_same_profile_to_its_own_address_is_fine(self):
        """Re-running a command must not trip the duplicate check against the
        very profile being updated."""
        s = _store(("Bendy", "NA1", "bendy@example.com"))
        link_email.link(s, "Bendy#NA1", "bendy@example.com")
        assert s.list_profiles()[0]["email"] == "bendy@example.com"

    def test_clearing_removes_the_link(self):
        s = _store(("Bendy", "NA1", "bendy@example.com"))
        link_email.link(s, "Bendy#NA1", None)
        assert s.list_profiles()[0]["email"] is None

    def test_nothing_else_on_the_profile_changes(self):
        """The update is a read-modify-write of a whole record, so a dropped
        field would be silent — the profile would still work and its region or
        goal would simply be gone."""
        s = _store(("Bendy", "NA1", None))
        before = s.list_profiles()[0]
        link_email.link(s, "Bendy#NA1", "bendy@example.com")
        after = s.list_profiles()[0]
        for field in profiles.PROFILE_FIELDS:
            if field != "email":
                assert after[field] == before[field], field


class TestReport:
    def test_it_names_the_unlinked(self):
        s = _store(("Bendy", "NA1", "bendy@example.com"), ("Friend", "KR", None))
        text = link_email.report(s)
        assert "not linked" in text
        assert "1 of 2 linked" in text

    def test_unlinked_profiles_come_first(self):
        """The list is a to-do list — the ones needing attention go on top."""
        s = _store(("Aaa", "NA1", "a@example.com"), ("Zzz", "NA1", None))
        text = link_email.report(s)
        assert text.index("Zzz") < text.index("Aaa")

    def test_an_empty_store_says_so_rather_than_crashing(self):
        assert "seed_profiles" in link_email.report(_store())


class TestResolutionActuallyUsesIt:
    def test_a_linked_profile_is_the_one_a_signed_in_user_lands_on(self):
        """End to end: the point of the link is this behaviour, and asserting
        the stored field alone would pass even if nothing read it."""
        s = _store(("Aaa", "NA1", None), ("Bendy", "NA1", None))
        link_email.link(s, "Bendy#NA1", "bendy@example.com")
        active = profiles.resolve_active(
            s.list_profiles(), signed_in_email="bendy@example.com")
        assert active["game_name"] == "Bendy"

    def test_without_a_link_you_get_whoever_sorts_first(self):
        """The behaviour being fixed, pinned so the fix is visibly a fix."""
        s = _store(("Aaa", "NA1", None), ("Bendy", "NA1", None))
        active = profiles.resolve_active(
            s.list_profiles(), signed_in_email="bendy@example.com")
        assert active["game_name"] == "Aaa"
