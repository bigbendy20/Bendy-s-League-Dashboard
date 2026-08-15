"""
Deployment configuration.

Everything here fails only at deploy time — a missing dependency, a secret
that isn't wired, a file that shouldn't be committed. That class of problem
is invisible to every other test in the suite because nothing imports or
executes it locally, which is exactly why it's worth pinning statically.
"""
import pathlib


ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestRequirements:
    def test_streamlit_requests_the_auth_extra(self):
        """`st.login()` needs Authlib, which plain `streamlit` doesn't pull
        in. The app imports and runs fine without it and then fails at the
        moment a real user clicks sign in — in production, only."""
        assert "streamlit[auth]" in _active_lines(ROOT / "requirements.txt")

    def test_the_postgres_driver_is_listed(self):
        """Community Cloud installs from this file; without it the hosted
        store can't open and every profile silently reads empty."""
        assert "psycopg" in _active_lines(ROOT / "requirements.txt")


class TestGitignore:
    """The three files that must never reach a public repo."""

    def test_secrets_are_ignored(self):
        ignored = (ROOT / ".gitignore").read_text()
        for path in (".env", "roster.txt", ".streamlit/secrets.toml"):
            assert path in ignored, f"{path} is not gitignored"

    def test_an_example_exists_for_each_ignored_file(self):
        """A gitignored file with no committed example is a setup step
        nobody can discover from the repository."""
        assert (ROOT / "roster.example.txt").exists()
        assert (ROOT / ".streamlit" / "secrets.example.toml").exists()

    def test_no_real_secret_is_committed_by_example(self):
        """The examples are templates. A real key pasted into one would be
        published, and it would look like documentation."""
        for name in ("roster.example.txt", ".streamlit/secrets.example.toml"):
            text = (ROOT / name).read_text()
            assert "RGAPI-" not in text, f"{name} contains something key-shaped"


def _active_lines(path) -> str:
    """File contents with comment lines and trailing comments removed.

    Checking raw text for `"concurrency:"` passes just as happily on
    `"# concurrency:"` — commenting the guard out was invisible to the first
    version of these tests. Stripping comments first is what makes them
    assert the configuration rather than the file's vocabulary.
    """
    lines = []
    for line in pathlib.Path(path).read_text().splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines.append(line.split(" #", 1)[0])
    return "\n".join(lines)


class TestWorkflow:
    WORKFLOW = ROOT / ".github" / "workflows" / "refresh.yml"

    def test_the_workflow_exists(self):
        assert self.WORKFLOW.exists()

    def test_it_reads_both_secrets(self):
        """Either one missing produces a job that runs, succeeds, and
        refreshes nothing."""
        text = _active_lines(self.WORKFLOW)
        assert "secrets.RIOT_API_KEY" in text
        assert "secrets.DATABASE_URL" in text

    def test_concurrency_is_limited(self):
        """Two overlapping runs would double-spend the shared Riot rate
        limit, which the app has no way to detect or recover from."""
        assert "concurrency:" in _active_lines(self.WORKFLOW)

    def test_it_installs_what_the_job_actually_imports(self):
        """The refresher needs riotwatcher, pandas and a Postgres driver —
        and deliberately not Streamlit, since it never renders anything.
        Running 288 times a day, install time is most of the cost."""
        text = _active_lines(self.WORKFLOW)
        for package in ("riotwatcher", "pandas", "psycopg"):
            assert package in text
        assert "pip install streamlit" not in text


class TestDeployGuide:
    def test_the_guide_exists(self):
        assert (ROOT / "DEPLOY.md").exists()

    def test_it_covers_the_steps_that_have_an_ordering_trap(self):
        """The callback URL can't be registered until the app is deployed,
        and the backfill should finish before launch. Both are the kind of
        thing you only discover by hitting them."""
        text = (ROOT / "DEPLOY.md").read_text()
        for topic in ("oauth2callback", "--backfill", "DATABASE_URL",
                      "public", "consent", "upload_store.py", "resumable",
                      "OneDrive"):
            assert topic in text, f"DEPLOY.md doesn't mention {topic}"


class TestNoRealPlayersInTrackedFiles:
    """The roster must not leak into a public repo through the back door.

    `roster.txt` is gitignored, which protects the list itself and nothing
    else. Real Riot IDs had reached three tracked files anyway — used as test
    fixtures because they were the awkward cases to hand: a name with spaces,
    a lowercase tag, a tag that is also a region. Convenient, and it published
    exactly what the gitignore existed to prevent.

    The fixtures now use invented names with the same awkward shapes, which
    is strictly better: the shape is the thing under test, and the identity
    was never load-bearing.
    """

    def _tracked_text(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        skip = {"__pycache__", ".git", "data", "highlight_replays", ".venv"}
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if set(path.parts) & skip:
                continue
            if path.name in ("roster.txt", ".env") or path.suffix in (".png", ".db", ".pyc"):
                continue
            if ".streamlit/secrets.toml" in path.as_posix():
                continue
            try:
                yield path, path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

    def test_no_name_from_the_roster_appears_anywhere_committable(self):
        import os
        import pathlib

        import roster

        roster_path = pathlib.Path(__file__).resolve().parent.parent / "roster.txt"
        if not roster_path.exists():
            return          # nothing to protect on a fresh clone

        entries = roster.parse(roster_path.read_text(encoding="utf-8"))
        names = {e["game_name"] for e in entries if len(e["game_name"]) > 3}
        assert names, "roster parsed to nothing — the check would be vacuous"

        offenders = []
        for path, text in self._tracked_text():
            for name in names:
                if name in text:
                    offenders.append(f"{os.path.basename(path)}: {name}")
        assert not offenders, "real Riot IDs in committable files: " + ", ".join(offenders)
