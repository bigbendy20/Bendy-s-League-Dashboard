"""
Executes `app.py` top to bottom with Streamlit and Riot stubbed out.

This is the test that should have existed before the module split. Three
startup crashes shipped in a row — a stray `st.markdown` at import time, a
constant (`HEADER_FONT`) left behind in app.py, and runtime state being
bound *after* the onboarding form that needs it — and none of the earlier
checks could catch any of them, because they all inspected the code without
running it. Ordering bugs only appear when you execute in order.

The stubs are permissive on purpose: the goal is to reach the end of the
script, not to assert on what got rendered. What it proves is narrow but
exactly what kept breaking — every name resolves along the real startup
path, in the real order.

Two paths matter and both are covered: the unconfigured path (no API key →
onboarding form → `st.stop()`), and the configured path (fetch → control
bar → binding → navigation).
"""
import pathlib
import sys
import tempfile
import types


class _Stub:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return _Stub()

    def __call__(self, *args, **kwargs):
        return _Stub()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter([])

    def __or__(self, other):
        return _Stub()

    def __ror__(self, other):
        return _Stub()

    def __bool__(self):
        return False

    def __len__(self):
        return 0


class _SessionState(dict):
    def __getattr__(self, name):
        if name not in self:
            raise AttributeError(name)
        return self[name]

    def __setattr__(self, name, value):
        self[name] = value


class _SignedInUser:
    """`st.user`-shaped. Real attributes, because `auth.gate` reads them."""

    def __init__(self, email="", is_logged_in=False):
        self.email = email
        self.is_logged_in = is_logged_in


class _StreamlitStub(types.ModuleType):
    """Enough Streamlit to let the script run start to finish."""

    def __init__(self, name):
        super().__init__(name)
        self.session_state = _SessionState()
        # Real containers, not stubs. `st.secrets` returning a stub made
        # `parse_allowlist` yield an empty set, which correctly denied
        # everyone — so every startup test failed for the right reason and
        # told you nothing about what it was actually testing.
        self.secrets = {}
        self.user = _SignedInUser()

    def __getattr__(self, name):
        if name == "columns":
            # Must return the right number of columns — the script unpacks
            # these into tuples, so a single stub would raise.
            return lambda spec, **kw: [
                _Stub() for _ in (spec if isinstance(spec, (list, tuple)) else range(spec))
            ]
        if name in ("cache_data", "cache_resource"):
            def decorator(*args, **kwargs):
                def wrap(fn):
                    fn.clear = lambda: None
                    return fn
                return wrap(args[0]) if args and callable(args[0]) else wrap
            return decorator
        if name in ("selectbox", "radio", "select_slider"):
            # Return a real option, not a stub — the script compares the result
            # against strings ("All", queue names) and indexes DataFrames with it.
            def pick(label, options=(), index=0, **kw):
                options = list(options)
                return options[index] if options else None
            return pick
        if name == "multiselect":
            return lambda label, options=(), default=None, **kw: list(default or [])
        if name == "text_input":
            return lambda label, value="", **kw: value
        if name in ("checkbox", "toggle"):
            return lambda label, value=False, **kw: value
        if name in ("slider", "number_input"):
            return lambda label, *a, value=0, **kw: value
        if name == "fragment":
            # A real decorator, not a stub. `@st.fragment(run_every=...)` on a
            # stub swallows the function whole, so the auto-refresh pollers
            # would never execute and the simulation would "pass" without ever
            # entering the code it exists to check.
            def fragment(func=None, **kwargs):
                if func is not None and callable(func):
                    return func
                return lambda f: f
            return fragment
        if name == "rerun":
            # st.rerun() aborts the script in Streamlit. Raising keeps the
            # control flow honest instead of letting execution fall through
            # code that would never run.
            def _rerun(*a, **kw):
                raise SystemExit("st.rerun()")
            return _rerun
        if name == "stop":
            def _stop():
                raise SystemExit("st.stop()")
            return _stop
        return _Stub()


def _install_stubs(fresh: bool = True):
    # On a rerun Streamlit is the same module object, and so is session_state.
    if not fresh and isinstance(sys.modules.get("streamlit"), _StreamlitStub):
        return sys.modules["streamlit"]
    st = _StreamlitStub("streamlit")
    sys.modules["streamlit"] = st
    for name in ("plotly", "plotly.express", "plotly.graph_objects", "riotwatcher",
                 "dotenv", "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont",
                 "requests"):
        module = types.ModuleType(name)
        module.__getattr__ = lambda _n: _Stub()
        sys.modules[name] = module
    sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
    sys.modules["riotwatcher"].LolWatcher = _Stub
    sys.modules["riotwatcher"].RiotWatcher = _Stub
    return st


# The exec'd script's namespace from the last run. Lets tests assert on what
# app.py *computed*, not just that it finished — a wiring change that stops
# calling a helper leaves every "did it run" assertion perfectly happy.
LAST_GLOBALS: dict = {}


def _run_app(env: dict, seed: dict | None = None, fresh: bool = True,
             registered_profiles: list | None = None,
             stored_matches: dict | None = None,
             signed_in: str | None = "bendy@example.com",
             allowlist: str | None = "bendy@example.com"):
    """Execute app.py with the given environment. Returns None on a clean
    finish, or the SystemExit reason if the script called st.stop().

    `fresh=False` models a Streamlit *rerun*: the script re-executes but the
    already-imported modules keep whatever state the last run left in them.
    That distinction matters — see TestRerun below.
    """
    import os

    st = _install_stubs(fresh=fresh)
    if fresh:
        for module in ("theme_css", "layout", "components", "views", "runtime"):
            sys.modules.pop(module, None)
    # Auth is configured before the script runs, exactly as the platform
    # would. `signed_in=None` produces an anonymous visitor, which is a real
    # scenario worth being able to simulate rather than something to bypass.
    st.secrets = {"allowed_emails": allowlist} if allowlist else {}
    st.user = _SignedInUser(email=signed_in or "", is_logged_in=bool(signed_in))
    if seed:
        st.session_state.update(seed)

    saved = {k: os.environ.get(k) for k in env}
    os.environ.update({k: v for k, v in env.items() if v is not None})
    for key, value in env.items():
        if value is None:
            os.environ.pop(key, None)

    global LAST_GLOBALS
    app_path = pathlib.Path(__file__).resolve().parent.parent / "app.py"
    sys.path.insert(0, str(app_path.parent))
    try:
        source = app_path.read_text()
        # `__file__` points into a throwaway directory, NOT the real project.
        # app.py derives the .env path from it, and `update_env_value` writes
        # to that path — so with the real path here, simply *running* the test
        # suite rewrote the developer's own .env. It did: an early version of
        # this file left `HERO_CHAMPION=<_Stub object at 0x...>` in a live
        # config, because the stubbed Settings dropdown returned a stub and
        # the script dutifully saved it.
        #
        # Only two things key off __file__ (the .env path and the favicon),
        # and the favicon is already guarded by os.path.exists — so redirecting
        # it is safe. Imports still resolve, since those come from sys.path.
        sandbox = tempfile.mkdtemp(prefix="lolboard-startup-")
        if registered_profiles is not None:
            # app.py reads profiles from `<app dir>/data/profiles`, and the
            # app dir is this sandbox — so seeding here gives the run a real
            # multi-profile store without touching the developer's data.
            import store as _store

            # Seeded through the same factory the app uses, into the sandbox.
            # It was a FileStore at `<app dir>/data/profiles`, matching what
            # app.py hardcoded — so the harness agreed with the app about a
            # store neither the deployed site nor the refresher would ever
            # use. Both now go through `open_store`, which means this fixture
            # exercises the real path instead of a parallel one.
            seeded = _store.open_store(base_dir=sandbox)
            for profile in registered_profiles:
                seeded.upsert_profile(profile)
            for puuid, rows in (stored_matches or {}).items():
                seeded.save_matches(puuid, rows)
        globals_ = {"__name__": "__main__",
                    "__file__": str(pathlib.Path(sandbox) / "app.py")}
        LAST_GLOBALS = globals_
        exec(compile(source, "app.py", "exec"), globals_)
        return None
    except SystemExit as exc:
        return str(exc)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class TestStartup:
    def test_unconfigured_path_reaches_onboarding(self):
        """No API key: must render the onboarding form and stop — without a
        NameError. This is the path that broke on `HERO_CHAMPION`, because
        the form renders before runtime state was bound."""
        result = _run_app({
            "RIOT_API_KEY": "", "RIOT_GAME_NAME": "", "RIOT_TAG_LINE": "",
        })
        assert result is not None, "expected st.stop() at the onboarding form"
        assert "stop" in result.lower()

    def test_configured_path_runs_to_navigation(self):
        """With config present the script should execute all the way through
        the control bar, the (stubbed) fetch, binding and navigation."""
        result = _run_app({
            "RIOT_API_KEY": "RGAPI-test-key",
            "RIOT_GAME_NAME": "Tester",
            "RIOT_TAG_LINE": "NA1",
            "PLATFORM_REGION": "na1",
            "CONTINENTAL_REGION": "americas",
        })
        # A stubbed fetch yields no data, so the script legitimately stops at
        # the "no match data loaded" guard. Reaching that point means every
        # name up to and including the control bar resolved.
        assert result is None or "stop" in result.lower()


def _seeded_state():
    """Session state as it looks once a fetch has succeeded.

    Seeding it is what lets the test get past the `if df.empty` guard and
    reach the phase-2 bind, which is where the rerun-pollution bug lived.
    """
    import pandas as pd

    # Deliberately mixed: one ranked game and one ARAM. The script has to
    # split them and still reach navigation — a scoping bug that emptied the
    # core frame would otherwise only show up in the browser.
    df = pd.DataFrame([
        {
            "win": True, "champion": "Ahri", "queue_id": 420,
            "queue_category": "Ranked", "game_mode": "CLASSIC",
            "position": "MIDDLE", "role_label": "Mid",
            "match_id": "NA1_1", "game_start": 1_700_000_000_000,
            "duration_min": 28.0, "kills": 5, "deaths": 2, "assists": 7,
        },
        {
            "win": False, "champion": "Sona", "queue_id": 450,
            "queue_category": "ARAM", "game_mode": "ARAM",
            "position": "", "role_label": "ARAM",
            "match_id": "NA1_2", "game_start": 1_700_000_100_000,
            "duration_min": 18.0, "kills": 2, "deaths": 6, "assists": 11,
        },
    ])
    return {
        "df": df, "league_entries": [],
        # A real profileIconId, so the configured path exercises the profile
        # icon branch rather than only its fallback.
        "summoner": {"profileIconId": 4568, "summonerLevel": 300},
        "puuid": "p", "mastery": [],
    }


class TestRerun:
    """Streamlit re-executes app.py on every interaction, but modules stay
    imported. Anything app.py writes into a module therefore survives into the
    next run — and if app.py then *reads* that module back, run 2 sees state
    run 1 created. This shipped: the bind() splat used `dir(components)`, which
    on rerun returned the names bind() had injected, re-passing `render_hero`
    and raising TypeError on the user's first click.
    """

    ENV = {
        "RIOT_API_KEY": "RGAPI-test-key", "RIOT_GAME_NAME": "Tester",
        "RIOT_TAG_LINE": "NA1", "PLATFORM_REGION": "na1",
        "CONTINENTAL_REGION": "americas",
    }

    def test_second_run_matches_first(self):
        first = _run_app(self.ENV, seed=_seeded_state())
        second = _run_app(self.ENV, seed=_seeded_state(), fresh=False)
        assert first == second, (
            f"rerun diverged from first load: {first!r} then {second!r}"
        )

    def test_component_exports_survive_binding(self):
        """The frozen export list must not absorb injected names."""
        _install_stubs()
        for module in ("layout", "components", "views", "runtime"):
            sys.modules.pop(module, None)
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        import components
        import runtime

        before = components.EXPORTS
        runtime.bind(render_hero=lambda *a, **k: None, some_value=1)
        assert components.EXPORTS == before, "EXPORTS changed after a bind"
        assert "render_hero" not in components.EXPORTS, (
            "render_hero is defined in layout.py — if it appears in components' "
            "exports it will be passed to bind() twice"
        )


class TestHeroWiring:
    """app.py has to actually use the icon helpers, not just define them.

    Dropping the `site_icon_url` call and hardcoding champion art passed the
    whole suite: the helper stayed correct and unit-tested, and nothing
    checked that the script still called it.
    """

    ENV = {
        "RIOT_API_KEY": "RGAPI-test-key", "RIOT_GAME_NAME": "Tester",
        "RIOT_TAG_LINE": "NA1", "PLATFORM_REGION": "na1",
        "CONTINENTAL_REGION": "americas", "HERO_CHAMPION": "",
    }

    def test_hero_icon_resolves_to_the_profile_icon(self):
        _run_app(self.ENV, seed=_seeded_state())
        assert "profileicon/4568" in LAST_GLOBALS.get("hero_icon_url", "")

    def test_background_follows_most_played_on_auto(self):
        """With HERO_CHAMPION unset the splash should come from the seeded
        match history, not the hardcoded fallback."""
        _run_app(self.ENV, seed=_seeded_state())
        assert LAST_GLOBALS.get("HERO_CHAMPION") == "Ahri"
        assert "Ahri" in LAST_GLOBALS.get("hero_url", "")

    def test_explicit_pick_overrides_auto(self):
        env = dict(self.ENV, HERO_CHAMPION="Zed")
        _run_app(env, seed=_seeded_state())
        assert LAST_GLOBALS.get("HERO_CHAMPION") == "Zed"


class TestHarnessSafety:
    """The test suite must not touch the real .env.

    This is not hypothetical: running the suite used to overwrite
    HERO_CHAMPION in the developer's live config with a stub object's repr.
    A test that silently edits the machine it runs on is worse than no test.
    """

    def test_running_the_app_leaves_the_real_env_alone(self):
        env_path = pathlib.Path(__file__).resolve().parent.parent / ".env"
        before = env_path.read_bytes() if env_path.exists() else None
        _run_app({
            "RIOT_API_KEY": "RGAPI-test-key", "RIOT_GAME_NAME": "Tester",
            "RIOT_TAG_LINE": "NA1", "PLATFORM_REGION": "na1",
            "CONTINENTAL_REGION": "americas",
        }, seed=_seeded_state())
        after = env_path.read_bytes() if env_path.exists() else None
        assert before == after, "the startup simulation modified the real .env"

    def test_config_writes_are_redirected_away_from_the_project(self):
        """Checks the redirect *before* writing anything.

        The first version of this test called update_env_value and then
        asserted the value landed in the file — which, with the redirect
        removed, happily wrote to the real .env and passed. Verifying the
        target path first is the whole point: a safety test that performs the
        unsafe act before checking whether it's safe isn't a safety test.
        """
        project_dir = pathlib.Path(__file__).resolve().parent.parent
        _run_app({
            "RIOT_API_KEY": "RGAPI-test-key", "RIOT_GAME_NAME": "Tester",
            "RIOT_TAG_LINE": "NA1", "PLATFORM_REGION": "na1",
            "CONTINENTAL_REGION": "americas",
        }, seed=_seeded_state())

        target_dir = pathlib.Path(LAST_GLOBALS["__file__"]).resolve().parent
        assert target_dir != project_dir, (
            f"app.py would write config into the real project at {target_dir}"
        )

        # Only now that the target is known-safe, prove writes actually work
        # there — otherwise the check above could pass on a broken path.
        update = LAST_GLOBALS.get("update_env_value")
        assert update is not None
        update("HERO_CHAMPION", "Teemo")
        assert "Teemo" in (target_dir / ".env").read_text()


class TestQueueScopingWiring:
    """app.py must actually split the frame, not just import the helpers."""

    ENV = {
        "RIOT_API_KEY": "RGAPI-test-key", "RIOT_GAME_NAME": "Tester",
        "RIOT_TAG_LINE": "NA1", "PLATFORM_REGION": "na1",
        "CONTINENTAL_REGION": "americas", "HERO_CHAMPION": "",
    }

    def test_core_frame_excludes_other_modes(self):
        _run_app(self.ENV, seed=_seeded_state())
        core = LAST_GLOBALS.get("df")
        other = LAST_GLOBALS.get("other_modes_df")
        assert core is not None and other is not None
        assert list(core["queue_id"]) == [420]
        assert list(other["queue_id"]) == [450]

    def test_nothing_is_lost_in_the_split(self):
        _run_app(self.ENV, seed=_seeded_state())
        total = len(LAST_GLOBALS["all_df"])
        assert len(LAST_GLOBALS["df"]) + len(LAST_GLOBALS["other_modes_df"]) == total

    def test_background_ignores_other_modes(self):
        """Most-played is computed off the core frame, so an ARAM-only
        champion must not end up as the site background."""
        _run_app(self.ENV, seed=_seeded_state())
        assert LAST_GLOBALS.get("HERO_CHAMPION") == "Ahri"


class TestAutoRefresh:
    """The five-minute pollers.

    Off by default, so the ordinary startup tests never touch them. This
    turns them on, which means the fragment decorator has to be a real
    decorator in the stub — otherwise the poller bodies are swallowed and
    every assertion here would pass without executing anything.
    """

    ENV = {
        "RIOT_API_KEY": "RGAPI-test-key", "RIOT_GAME_NAME": "Tester",
        "RIOT_TAG_LINE": "NA1", "PLATFORM_REGION": "na1",
        "CONTINENTAL_REGION": "americas", "HERO_CHAMPION": "",
    }

    def test_off_by_default(self):
        _run_app(self.ENV, seed=_seeded_state())
        st = sys.modules["streamlit"]
        assert st.session_state.get("auto_refresh") is False

    def test_poll_interval_is_five_minutes(self):
        _run_app(self.ENV, seed=_seeded_state())
        assert LAST_GLOBALS.get("POLL_SECONDS") == 300

    def test_startup_survives_auto_refresh_enabled(self):
        """With polling on, the script must still reach navigation. The
        pollers run inline here because a stubbed Riot client returns no new
        ids, so nothing escalates to a rerun."""
        seed = dict(_seeded_state(), auto_refresh=True)
        result = _run_app(self.ENV, seed=seed)
        assert result is None or "stop" in result.lower()

    def test_loaded_match_ids_are_recorded_for_the_poll(self):
        """The poll diffs against this. If it's never populated, every poll
        reports every game as new and triggers a full refresh every time."""
        _run_app(self.ENV, seed=_seeded_state())
        loaded = LAST_GLOBALS.get("st").session_state.get("loaded_match_ids")
        assert loaded and set(loaded) == {"NA1_1", "NA1_2"}


class TestProfileResolution:
    """The profile layer, exercised through a real run of app.py.

    Unit tests cover the resolution rules; these cover the wiring, which is
    where this project's regressions actually live.
    """

    ENV = {
        "RIOT_API_KEY": "RGAPI-test-key", "RIOT_GAME_NAME": "Tester",
        "RIOT_TAG_LINE": "NA1", "PLATFORM_REGION": "na1",
        "CONTINENTAL_REGION": "americas", "HERO_CHAMPION": "",
    }

    def test_env_bootstraps_a_single_profile(self):
        """Local mode must keep working with no store and no database —
        otherwise the offline path dies the moment hosting work starts."""
        _run_app(self.ENV, seed=_seeded_state())
        registered = LAST_GLOBALS.get("registered")
        assert len(registered) == 1
        assert registered[0]["game_name"] == "Tester"

    def test_active_profile_drives_the_riot_id(self):
        """GAME_NAME/TAG_LINE now come from the resolved profile rather than
        straight from the environment. If that wiring breaks, the app fetches
        the wrong account while looking entirely healthy."""
        _run_app(self.ENV, seed=_seeded_state())
        assert LAST_GLOBALS.get("GAME_NAME") == "Tester"
        assert LAST_GLOBALS.get("TAG_LINE") == "NA1"

    def test_api_key_is_not_part_of_a_profile(self):
        """One key serves every profile and must stay server-side. A key on
        the profile record would end up in the store, and the store is the
        thing that gets backed up and copied around."""
        _run_app(self.ENV, seed=_seeded_state())
        for profile in LAST_GLOBALS.get("registered", []):
            assert "api_key" not in profile
            assert "RGAPI" not in str(profile.values())

    def test_unconfigured_env_yields_no_profiles(self):
        result = _run_app({"RIOT_API_KEY": "", "RIOT_GAME_NAME": "",
                           "RIOT_TAG_LINE": ""})
        assert result is not None and "stop" in result.lower()


class TestMultipleProfiles:
    """Hosted mode: several friends registered, any of them viewable.

    These need a seeded store because in local mode the environment and the
    bootstrapped profile hold identical values — so reading the identity from
    the wrong one is invisible. Two profiles with different names is the
    smallest setup where that distinction can fail.
    """

    ENV = {
        "RIOT_API_KEY": "RGAPI-test-key", "RIOT_GAME_NAME": "EnvName",
        "RIOT_TAG_LINE": "ENV", "PLATFORM_REGION": "na1",
        "CONTINENTAL_REGION": "americas", "HERO_CHAMPION": "",
    }
    REGISTERED = [
        # Deliberately on EUW while the environment says NA. Every field on
        # the active profile has to differ from its env counterpart, or a
        # mutation that reads the env instead passes unnoticed — which is
        # exactly what happened to the region on the first attempt.
        {"puuid": "p1", "game_name": "Bendy", "tag_line": "EUW",
         "platform_region": "euw1", "continental_region": "europe",
         "display_name": "Bendy", "email": "bendy@example.com"},
        {"puuid": "p2", "game_name": "Friend", "tag_line": "KR",
         "platform_region": "kr", "continental_region": "asia",
         "display_name": "Friend", "email": "friend@example.com"},
    ]

    def test_store_profiles_replace_the_env_identity(self):
        """The wiring that matters. With a store configured, the environment
        is no longer the source of identity — only the API key comes from it.
        Reading `RIOT_GAME_NAME` here instead would fetch the wrong account
        while every page still rendered normally."""
        _run_app(self.ENV, seed=_seeded_state(), registered_profiles=self.REGISTERED)
        assert LAST_GLOBALS.get("GAME_NAME") == "Bendy"
        assert LAST_GLOBALS.get("GAME_NAME") != "EnvName"

    def test_all_registered_profiles_are_offered(self):
        _run_app(self.ENV, seed=_seeded_state(), registered_profiles=self.REGISTERED)
        assert len(LAST_GLOBALS.get("registered", [])) == 2

    def test_region_follows_the_active_profile(self):
        """A friend on EUW must be fetched against EUW routing. Falling back
        to the env's `na1` would return 404s that look like a missing
        account — and the assertion has to be able to tell the two apart,
        which is why the fixture region differs from the environment's."""
        _run_app(self.ENV, seed=_seeded_state(), registered_profiles=self.REGISTERED)
        assert LAST_GLOBALS.get("PLATFORM_REGION") == "euw1"
        assert LAST_GLOBALS.get("CONTINENTAL_REGION") == "europe"
        assert LAST_GLOBALS.get("TAG_LINE") == "EUW"


class TestAccessGate:
    """The gate, exercised through a real run of app.py.

    `auth.py` covers the rules; these cover the thing unit tests can't see —
    that the gate sits *before* any data loads or renders. A check placed
    after the fetch would still leak: the page would briefly draw, and the
    shared API budget would be spent on a stranger.
    """

    ENV = {
        "RIOT_API_KEY": "RGAPI-test-key", "RIOT_GAME_NAME": "Tester",
        "RIOT_TAG_LINE": "NA1", "PLATFORM_REGION": "na1",
        "CONTINENTAL_REGION": "americas", "HERO_CHAMPION": "",
    }

    def test_an_anonymous_visitor_is_stopped(self):
        result = _run_app(self.ENV, seed=_seeded_state(), signed_in=None)
        assert result is not None and "stop" in result.lower()

    def test_an_unlisted_account_is_stopped(self):
        """Signing in with Google proves you have a Gmail address. Four
        billion people do."""
        result = _run_app(self.ENV, seed=_seeded_state(),
                          signed_in="stranger@example.com")
        assert result is not None and "stop" in result.lower()

    def test_a_listed_account_gets_through(self):
        result = _run_app(self.ENV, seed=_seeded_state(),
                          signed_in="bendy@example.com")
        assert result is None or "rerun" in result.lower()

    def test_a_missing_allowlist_locks_the_site(self):
        """The failure direction that matters. A typo'd secret name yields no
        allow-list, and treating that as 'no checks needed' would open the
        site silently."""
        result = _run_app(self.ENV, seed=_seeded_state(),
                          signed_in="bendy@example.com", allowlist=None)
        assert result is not None and "stop" in result.lower()

    def test_no_data_is_loaded_for_a_rejected_visitor(self):
        """The gate is placed before the fetch, not after. Otherwise a
        stranger's page load still costs API budget and still briefly
        renders someone's stats."""
        _run_app(self.ENV, seed=_seeded_state(), signed_in="stranger@example.com")
        assert "all_df" not in LAST_GLOBALS
        assert "df" not in LAST_GLOBALS

    def test_the_signed_in_email_reaches_profile_resolution(self):
        """The whole point of OIDC over Streamlit's private-app invites: the
        app knows who you are, so it can open on your own page."""
        _run_app(self.ENV, seed=_seeded_state(), signed_in="Bendy@Example.COM")
        assert LAST_GLOBALS.get("signed_in_email") == "bendy@example.com"


class TestTheSiteReadsTheStore:
    """Where the match data on the page comes from.

    This is the gap that would have shipped. `app.py` fetched from Riot on
    every load and never read stored matches at all — so the deployed site
    would have shown whatever Riot still *lists* (measured: 620 ids, about
    six months) while the database held 5,766 games back to September 2024,
    including 1,858 Riot will no longer enumerate. Every page would have
    rendered perfectly and been wrong.

    Nothing caught it because every existing startup test asserts on identity,
    regions and layout — none on the provenance of `df`. So these fix the
    fixture where correct and broken must disagree: the store holds games the
    stubbed API cannot return.
    """

    ENV = {
        "RIOT_API_KEY": "RGAPI-test-key", "RIOT_GAME_NAME": "Bendy",
        "RIOT_TAG_LINE": "NA1", "PLATFORM_REGION": "na1",
        "CONTINENTAL_REGION": "americas", "HERO_CHAMPION": "",
    }
    PROFILES = [
        {"puuid": "p1", "game_name": "Bendy", "tag_line": "NA1",
         "platform_region": "na1", "continental_region": "americas",
         "display_name": "Bendy", "email": "bendy@example.com"},
    ]

    def _rows(self, n):
        import stats
        from conftest import make_match

        return [stats.parse_match(make_match(match_id=f"NA1_{i}", puuid="p1"), "p1")
                for i in range(n)]

    def test_stored_matches_reach_the_page(self):
        _run_app(self.ENV, seed=_seeded_state(), registered_profiles=self.PROFILES,
                 stored_matches={"p1": self._rows(37)})
        df = LAST_GLOBALS.get("st").session_state.get("df")
        assert df is not None and len(df) == 37, (
            "the page is not reading matches from the store")

    def test_the_whole_history_is_read_not_a_recent_window(self):
        """No cap on the read. A limit here would silently discard the older
        part of a record the site exists to preserve — and 1,000 would look
        fine against any fixture smaller than that, so the fixture is
        deliberately larger than every configured target."""
        big = self._rows(1200)
        _run_app(self.ENV, seed=_seeded_state(), registered_profiles=self.PROFILES,
                 stored_matches={"p1": big})
        df = LAST_GLOBALS.get("st").session_state.get("df")
        assert len(df) == 1200, f"read only {len(df)} of 1200 stored games"

    def test_an_empty_store_still_falls_back_to_fetching(self):
        """First run for a newly added profile: nothing collected yet. The
        page must not be blank until the refresher next wakes up.

        The fetch is made to return real matches so the two outcomes differ.
        Asserting only that `df` exists passed whether or not the fallback ran
        — `df` defaults to an empty frame — and a mutant deleting the fallback
        entirely survived. The number has to come from somewhere only the
        fallback could have supplied.
        """
        from conftest import make_match
        import riot_client

        fetched = [make_match(match_id=f"NA1_{i}", puuid="p1") for i in range(9)]
        original = riot_client.RiotClient.fetch_recent_matches
        riot_client.RiotClient.fetch_recent_matches = (
            lambda self, puuid, count=20, use_cache=True: fetched)
        try:
            _run_app(self.ENV, seed=_seeded_state(),
                     registered_profiles=self.PROFILES, stored_matches={})
        finally:
            riot_client.RiotClient.fetch_recent_matches = original

        df = LAST_GLOBALS.get("st").session_state.get("df")
        assert df is not None and len(df) == 9, (
            "an empty store must fall back to fetching, not render nothing")

    def test_the_fallback_result_is_saved_so_it_only_happens_once(self):
        """Otherwise every page view by that friend re-fetches, on a shared
        key, until the refresher happens to reach them."""
        import os

        from conftest import make_match
        import riot_client
        import store as _store

        fetched = [make_match(match_id=f"NA1_{i}", puuid="p1") for i in range(5)]
        original = riot_client.RiotClient.fetch_recent_matches
        riot_client.RiotClient.fetch_recent_matches = (
            lambda self, puuid, count=20, use_cache=True: fetched)
        try:
            _run_app(self.ENV, seed=_seeded_state(),
                     registered_profiles=self.PROFILES, stored_matches={})
        finally:
            riot_client.RiotClient.fetch_recent_matches = original

        app_dir = os.path.dirname(LAST_GLOBALS["__file__"])
        reopened = _store.open_store(base_dir=app_dir)
        assert len(reopened.load_matches("p1")) == 5, (
            "the fallback fetch was not persisted")


class TestTheSiteUsesTheConfiguredDatabase:
    def test_the_store_is_opened_through_the_factory(self):
        """`app.py` hardcoded `FileStore(<app dir>/data/profiles)` and never
        consulted `DATABASE_URL`. Deployed, that reads an empty directory on
        an ephemeral disk: eight profiles, no games, and a data-shaped
        problem that is really a configuration one.

        Checked by parsing the source rather than by matching text, since
        `store.FileStore` appearing in a comment or a fallback would satisfy a
        grep while leaving the call site wrong.
        """
        import ast

        app_path = pathlib.Path(__file__).resolve().parent.parent / "app.py"
        tree = ast.parse(app_path.read_text(encoding="utf-8"))

        assignment = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "data_store":
                        assignment = node.value
        assert assignment is not None, "app.py never assigns data_store"
        called = ast.dump(assignment)
        assert "open_store" in called, (
            "app.py builds a store directly instead of using store.open_store, "
            "so DATABASE_URL is ignored and the deployed site reads nothing")


class TestTheHeroTitleFollowsTheProfile:
    """Whose board does the page say it is?

    On a multi-profile site a fixed "Bendy's League Board" above a friend's
    stats is actively wrong: switch profiles and every number changes while
    the heading doesn't. The browser tab keeps the site name.
    """

    ENV = {
        "RIOT_API_KEY": "RGAPI-test-key", "RIOT_GAME_NAME": "EnvName",
        "RIOT_TAG_LINE": "ENV", "PLATFORM_REGION": "na1",
        "CONTINENTAL_REGION": "americas", "HERO_CHAMPION": "",
    }
    REGISTERED = [
        {"puuid": "p1", "game_name": "Bendy", "tag_line": "EUW",
         "platform_region": "euw1", "continental_region": "europe",
         "display_name": "Bendy", "email": "bendy@example.com"},
        {"puuid": "p2", "game_name": "Friend", "tag_line": "KR",
         "platform_region": "kr", "continental_region": "asia",
         "display_name": "Friend", "email": "friend@example.com"},
    ]

    def test_the_title_is_the_active_profiles_riot_id(self):
        _run_app(self.ENV, seed=_seeded_state(), registered_profiles=self.REGISTERED)
        assert LAST_GLOBALS.get("PROFILE_TITLE") == "Bendy#EUW"

    def test_it_is_not_the_site_name(self):
        """The assertion the mutant survives without: a title that reads
        `APP_TITLE` renders fine and looks reasonable, so only comparing
        against it catches the regression."""
        _run_app(self.ENV, seed=_seeded_state(), registered_profiles=self.REGISTERED)
        assert LAST_GLOBALS.get("PROFILE_TITLE") != LAST_GLOBALS.get("APP_TITLE")

    def test_it_is_not_taken_from_the_environment(self):
        """`RIOT_GAME_NAME` is deliberately different from every profile, so
        reading the env instead of the active profile is visible."""
        _run_app(self.ENV, seed=_seeded_state(), registered_profiles=self.REGISTERED)
        assert "EnvName" not in LAST_GLOBALS.get("PROFILE_TITLE", "")

    def test_it_falls_back_to_the_site_name_with_no_profile(self):
        """The onboarding path has no Riot ID yet, and `#` on its own would
        be a strange thing to put at the top of a page."""
        env = dict(self.ENV, RIOT_GAME_NAME=None, RIOT_TAG_LINE=None)
        _run_app(env, seed=_seeded_state())
        assert LAST_GLOBALS.get("PROFILE_TITLE") == LAST_GLOBALS.get("APP_TITLE")
