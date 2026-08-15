"""
Loading `.env`.

This module exists because of a real failure: the backfill printed
`RIOT_API_KEY is not set` while the key sat in `.env`, because the CLI
scripts were written against GitHub Actions — where the key is a real
environment variable — and never read the file the app reads.

So the tests worth having are less about parsing than about *who reads what
from where*: that the scripts consult the file at all, that a real
environment variable still wins, and that the path doesn't depend on the
working directory.
"""
import ast
import os
import tempfile

import env_file


def write(text):
    handle = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False,
                                         encoding="utf-8")
    handle.write(text)
    handle.close()
    return handle.name


class TestParse:
    def test_simple_pairs(self):
        assert env_file.parse("A=1\nB=2") == {"A": "1", "B": "2"}

    def test_comments_and_blanks_are_ignored(self):
        text = "# a comment\n\nA=1\n   \n# B=2\n"
        assert env_file.parse(text) == {"A": "1"}

    def test_a_commented_out_setting_does_not_load(self):
        """The distinguishing case. A parser that only stripped `#` from the
        start of a *token* would happily set B here, which is how a setting
        someone deliberately disabled comes back to life."""
        assert "B" not in env_file.parse("# B=2")

    def test_values_containing_equals_survive(self):
        """Postgres URLs carry query strings: `?sslmode=require`. Splitting on
        every `=` instead of the first would truncate the connection string
        into something that fails at connect time, far from the cause."""
        url = "postgresql://u:p@host/db?sslmode=require&x=1"
        assert env_file.parse(f"DATABASE_URL={url}") == {"DATABASE_URL": url}

    def test_quotes_are_stripped(self):
        assert env_file.parse('A="1"') == {"A": "1"}
        assert env_file.parse("A='1'") == {"A": "1"}

    def test_a_hash_inside_a_value_is_kept(self):
        """Riot IDs are `Name#Tag`, and the tag line is a legitimate value.
        Stripping everything after `#` would silently truncate it — the very
        field this project is built around."""
        assert env_file.parse("RIOT_TAG_LINE=#comet") == {"RIOT_TAG_LINE": "#comet"}
        assert env_file.parse("ID=Name#0408") == {"ID": "Name#0408"}

    def test_a_trailing_comment_is_dropped(self):
        assert env_file.parse("A=1 # why") == {"A": "1"}

    def test_a_trailing_comment_after_a_value_containing_a_hash(self):
        """The case that distinguishes "comment" from "part of the value",
        and the only one that does. Both preceding tests pass whether the
        split is on `" #"` or on `"#"` — the first has no hash in the value,
        the second has no comment — so neither notices a parser that
        truncates `LowerTag#comet` to `LowerTag`. This one has both."""
        assert env_file.parse("ID=Name#0408 # a note") == {"ID": "Name#0408"}

    def test_export_prefix_is_tolerated(self):
        assert env_file.parse("export A=1") == {"A": "1"}

    def test_a_line_with_no_equals_is_skipped(self):
        assert env_file.parse("nonsense\nA=1") == {"A": "1"}

    def test_whitespace_around_the_key_and_value(self):
        assert env_file.parse("  A =  1  ") == {"A": "1"}


class TestLoad:
    def test_missing_variables_are_filled_in(self):
        path = write("RIOT_API_KEY=RGAPI-abc")
        environ = {}
        assert env_file.load(path, environ) == ["RIOT_API_KEY"]
        assert environ["RIOT_API_KEY"] == "RGAPI-abc"

    def test_an_existing_variable_is_not_overwritten(self):
        """The rule that makes `set DATABASE_URL=…` and Actions secrets work.
        If the file won, overriding config from the shell would appear to do
        nothing, and the OneDrive workaround in DEPLOY.md would be advice
        that silently fails."""
        path = write("RIOT_API_KEY=from-file")
        environ = {"RIOT_API_KEY": "from-the-shell"}
        assert env_file.load(path, environ) == []
        assert environ["RIOT_API_KEY"] == "from-the-shell"

    def test_an_empty_existing_value_is_treated_as_unset(self):
        """Windows `set VAR=` leaves an empty string rather than removing the
        variable, and every caller here does `.strip()` and checks falsiness.
        Treating empty as 'already set' would leave the key unfilled for the
        exact reason the user was trying to clear it."""
        environ = {"RIOT_API_KEY": ""}
        env_file.load(write("RIOT_API_KEY=from-file"), environ)
        assert environ["RIOT_API_KEY"] == "from-file"

    def test_a_missing_file_is_not_an_error(self):
        """Actions has no `.env` at all; the job must run anyway."""
        environ = {}
        assert env_file.load("/nonexistent/.env", environ) == []
        assert environ == {}

    def test_it_works_with_dotenv_stubbed_out(self):
        """This module used to delegate to `python-dotenv` when it was
        importable. The startup tests replace `dotenv` in `sys.modules` with a
        stub whose attributes are all no-op objects, so `dotenv_values()`
        returned something whose `.items()` yielded nothing — and the loader
        read the file, applied none of it, and reported success. Three tests
        here failed for that reason and nothing else did, which is the part
        worth remembering: the failure mode was silent everywhere except
        where something happened to assert on the result.

        Owning the parser removes the whole class of problem. Pinned so a
        future "just use the library" doesn't quietly reintroduce it.
        """
        import sys
        import types

        saved = sys.modules.get("dotenv")
        broken = types.ModuleType("dotenv")
        broken.dotenv_values = lambda *a, **k: {}
        sys.modules["dotenv"] = broken
        try:
            environ = {}
            assert env_file.load(write("A=1"), environ) == ["A"]
            assert environ == {"A": "1"}
        finally:
            if saved is None:
                del sys.modules["dotenv"]
            else:
                sys.modules["dotenv"] = saved

    def test_several_settings_at_once(self):
        environ = {}
        applied = env_file.load(write("A=1\nB=2\nC=3"), environ)
        assert sorted(applied) == ["A", "B", "C"]

    def test_the_default_path_sits_beside_the_code(self):
        """Not the working directory. `load_dotenv()` searches upward from
        the cwd, which is why `python lol-dashboard/refresh_job.py` from the
        parent folder found nothing while the `.bat` — which cd's first —
        worked.

        The assertion has to use a module somewhere *other* than the working
        directory. Checking `path_for(env_file.__file__)` looks like it tests
        this and doesn't: the suite runs from the project folder, so the
        correct answer and the cwd-based wrong answer are the same string.
        Mutation testing caught it — replacing the whole function body with
        `os.path.abspath(".env")` passed.
        """
        assert env_file.path_for("/somewhere/else/module.py") == "/somewhere/else/.env"
        assert os.path.isabs(env_file.path_for("relative/module.py"))
        # And, separately, that the real default is the one beside the code.
        expected = os.path.join(os.path.dirname(os.path.abspath(env_file.__file__)), ".env")
        assert env_file.path_for(env_file.__file__) == expected


class TestTheScriptsActuallyLoadIt:
    """The bug wasn't in the parser — it was that nobody called it.

    These parse the source rather than matching text, because a mention of
    `env_file` in a docstring or an import that's never called would satisfy
    a grep while reproducing the original failure exactly.
    """

    def source(self, name):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), name)
        with open(path, encoding="utf-8") as handle:
            return ast.parse(handle.read())

    def load_calls(self, tree):
        return [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "load"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "env_file"
        ]

    def calls_load(self, tree):
        return bool(self.load_calls(tree))

    def test_every_caller_anchors_the_path_to_its_own_file(self):
        """`env_file.load()` with no argument resolves relative to
        `env_file.py`, not to the caller. That's wrong in a way that only
        shows up under test: the startup tests point `app.py`'s `__file__` at
        a throwaway directory precisely so a test run can't read or write the
        developer's real `.env`, and a bare `load()` walked straight past that
        isolation. Two startup tests began passing or failing according to
        the contents of the local config — the exact class of
        environment-dependence this whole change set exists to remove.
        """
        for name in ("app.py", "seed_profiles.py", "refresh_job.py"):
            calls = self.load_calls(self.source(name))
            assert calls, name
            for call in calls:
                assert call.args, f"{name}: env_file.load() called with no path"
                argument = ast.dump(call.args[0])
                assert "path_for" in argument and "__file__" in argument, name

    def test_seed_profiles_loads_the_env_file(self):
        assert self.calls_load(self.source("seed_profiles.py"))

    def test_refresh_job_loads_the_env_file(self):
        assert self.calls_load(self.source("refresh_job.py"))

    def test_the_app_loads_the_env_file(self):
        assert self.calls_load(self.source("app.py"))

    def test_nothing_uses_load_dotenv_directly_any_more(self):
        """Two loaders reading one file is how the app and the jobs came to
        disagree about whether a key existed."""
        for name in ("app.py", "seed_profiles.py", "refresh_job.py", "upload_store.py"):
            for node in ast.walk(self.source(name)):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    assert node.func.id != "load_dotenv", name

    def test_the_key_check_names_the_file_it_looked_in(self):
        """"RIOT_API_KEY is not set" is true and useless when the key is
        sitting in `.env`. The message has to say where it looked."""
        for name in ("seed_profiles.py", "refresh_job.py"):
            path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), name)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            assert "path_for" in text, name


class TestSettingsWritesReachTheProcess:
    def test_update_env_value_also_sets_the_environment(self):
        """`env_file.load` never overwrites, so a key saved in the Settings
        panel would be written to the file and then ignored for the rest of
        the process — which is precisely the case the 401 re-key flow is
        for."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())

        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "update_env_value"
        )
        assigns_environ = any(
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Attribute)
                and target.value.attr == "environ"
                for target in node.targets
            )
            for node in ast.walk(function)
        )
        assert assigns_environ
