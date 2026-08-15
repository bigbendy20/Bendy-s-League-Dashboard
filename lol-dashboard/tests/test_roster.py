"""
Roster parsing.

The input is hand-written, so the tests are mostly about the ways a human
types a Riot ID wrong. Every rejection raises rather than skipping the line:
a silently dropped friend is a profile that never updates, and nobody notices
until they ask why their page is empty.
"""
import roster


class TestParseLine:
    def test_a_plain_riot_id(self):
        entry = roster.parse_line("Bendy#NA1")
        assert entry["game_name"] == "Bendy"
        assert entry["tag_line"] == "NA1"
        assert entry["display_name"] == "Bendy"

    def test_a_game_name_containing_spaces(self):
        """Riot allows spaces in game names, so splitting on whitespace would
        break real accounts — one of these friends is literally 'Two Word Name'."""
        entry = roster.parse_line("Two Word Name#Scout")
        assert entry["game_name"] == "Two Word Name"
        assert entry["tag_line"] == "Scout"

    def test_a_trailing_space_before_the_hash_is_stripped(self):
        """A copy-paste artifact that showed up in the real roster
        ('AnotherName #0408'). Riot names can't end in a space, so stripping is
        safe — and not stripping would make the account unfindable."""
        assert roster.parse_line("AnotherName #0408")["game_name"] == "AnotherName"

    def test_the_split_is_on_the_first_hash(self):
        """Game names can't contain a hash, so everything after the first one
        is the tag. Splitting on the last would mangle any tag with a hash."""
        entry = roster.parse_line("Name#a#b")
        assert entry["game_name"] == "Name" and entry["tag_line"] == "a#b"

    def test_a_lowercase_tag_is_preserved(self):
        """'LowerTag#comet' — tags aren't all uppercase and forcing the
        case would produce an account that doesn't exist."""
        assert roster.parse_line("LowerTag#comet")["tag_line"] == "comet"

    def test_an_optional_display_name(self):
        entry = roster.parse_line("Two Word Name#Scout = Scout")
        assert entry["game_name"] == "Two Word Name"
        assert entry["display_name"] == "Scout"

    def test_region_override(self):
        entry = roster.parse_line("Friend#EUW [euw1/europe]")
        assert entry["platform_region"] == "euw1"
        assert entry["continental_region"] == "europe"

    def test_continental_region_is_derived_not_trusted(self):
        """Only the platform is read; the continent comes from the table. A
        mismatched pair in the file would otherwise 404 during the backfill
        and read as a missing account."""
        entry = roster.parse_line("Friend#KR [kr/americas]")
        assert entry["continental_region"] == "asia"

    def test_default_region_is_na(self):
        entry = roster.parse_line("Bendy#NA1")
        assert entry["platform_region"] == "na1"
        assert entry["continental_region"] == "americas"

    def test_blank_and_comment_lines_are_skipped(self):
        assert roster.parse_line("") is None
        assert roster.parse_line("   ") is None
        assert roster.parse_line("# a comment") is None

    def test_a_missing_tag_is_an_error(self):
        try:
            roster.parse_line("JustAName")
        except roster.RosterError as exc:
            assert "#TAG" in str(exc)
        else:
            raise AssertionError("expected RosterError")

    def test_an_empty_tag_is_an_error(self):
        try:
            roster.parse_line("Name#")
        except roster.RosterError:
            pass
        else:
            raise AssertionError("expected RosterError")

    def test_an_unknown_region_is_an_error(self):
        """Caught here rather than as a confusing 404 hours into a backfill."""
        try:
            roster.parse_line("Name#TAG [nowhere/americas]")
        except roster.RosterError as exc:
            assert "region" in str(exc)
        else:
            raise AssertionError("expected RosterError")


class TestParseFile:
    REAL = """
    # Bendy's group
    AnotherName #0408
    PlainName#4389
    LowerTag#comet
    NameWithDigits2085#2085
    Two Word Name#Scout
    RegionTag#NA1
    DigitTag#1662
    """

    def test_the_real_roster_parses(self):
        entries = roster.parse(self.REAL)
        assert len(entries) == 7
        assert [e["game_name"] for e in entries] == [
            "AnotherName", "PlainName", "LowerTag", "NameWithDigits2085",
            "Two Word Name", "RegionTag", "DigitTag",
        ]

    def test_every_entry_has_what_seeding_needs(self):
        for entry in roster.parse(self.REAL):
            assert entry["game_name"] and entry["tag_line"]
            assert entry["platform_region"] in roster.REGIONS
            assert entry["continental_region"]

    def test_duplicates_are_rejected(self):
        """A duplicate is fetched twice and spends the shared budget twice to
        produce the same profile."""
        try:
            roster.parse("Bendy#NA1\nbendy#na1")
        except roster.RosterError as exc:
            assert "twice" in str(exc)
        else:
            raise AssertionError("expected RosterError")

    def test_errors_name_the_line_number(self):
        """A 30-line roster with one typo should say which line."""
        try:
            roster.parse("Good#NA1\n\nBroken\n")
        except roster.RosterError as exc:
            assert "line 3" in str(exc)
        else:
            raise AssertionError("expected RosterError")

    def test_an_empty_file_is_not_an_error(self):
        assert roster.parse("") == []
        assert roster.parse("# only comments\n") == []


class TestSeedScript:
    """The seeding entry point.

    Thin by design — parsing lives in `roster`, storage in `store` — so these
    only cover the two things that can't: that a dry run really makes no API
    calls, and that a bad roster fails before any network work rather than
    part-way through.
    """

    def _seed(self):
        import seed_profiles

        return seed_profiles

    def test_dry_run_imports_nothing_that_needs_the_riot_library(self):
        """`--dry-run` promises no API calls, and that has to hold on a
        machine where the Riot client isn't installed at all. A module-level
        `from refresh_job import ...` made the dry run crash on import,
        before it could print a single line — the opposite of what the flag
        is for. Checked structurally because the failure is about *where* the
        import sits, which no runtime assertion can see once it's fixed.
        """
        import ast
        import inspect

        # Parsed, not grepped. A first version searched the source text and
        # failed on the module docstring, which mentions `refresh_job` in
        # prose — the test was matching an explanation rather than an import.
        tree = ast.parse(inspect.getsource(self._seed()))
        top_level = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level.add(node.module.split(".")[0])
        assert "riot_client" not in top_level
        assert "refresh_job" not in top_level

    def test_a_missing_roster_exits_cleanly(self):
        seed = self._seed()
        assert seed.main(["--roster", "/nonexistent/roster.txt"]) == 2

    def test_a_broken_roster_fails_before_any_network_call(self):
        """Exit code 2 with a line number, not a partial seed. Re-running is
        idempotent, but half a roster is confusing state to be in."""
        import tempfile
        import os

        path = os.path.join(tempfile.mkdtemp(), "roster.txt")
        with open(path, "w") as handle:
            handle.write("Good#NA1\nBrokenLine\n")
        assert self._seed().main(["--roster", path]) == 2
