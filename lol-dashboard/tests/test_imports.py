"""
Every module imports cleanly, with Streamlit and friends stubbed out.

This exists because of a real failure: splitting `app.py` by line range
dragged a trailing `st.markdown(render_theme_css(...))` call into
`theme_css.py`, which then raised `NameError: name 'st' is not defined` the
moment `app.py` imported it. `py_compile` passed (it's valid syntax) and the
binding checker passed (it only looked at name resolution), so nothing
caught it before the app was launched.

Stubbing the third-party packages lets the import actually *execute* here,
which is what catches this class of bug. It doesn't prove the app renders —
only that nothing blows up at import time, which was the failure mode.
"""
import sys
import types


class _Stub:
    """Accepts anything. Enough for imports and decorators to resolve."""

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

    # Stubs turn up inside `SomeType | None` annotations, which real classes
    # handle and a bare object doesn't. Harness detail, not app behaviour.
    def __or__(self, other):
        return _Stub()

    def __ror__(self, other):
        return _Stub()


STUBBED = (
    "streamlit", "plotly", "plotly.express", "plotly.graph_objects",
    "riotwatcher", "dotenv", "PIL", "PIL.Image", "PIL.ImageDraw",
    "PIL.ImageFont", "requests",
)

APP_MODULES = (
    "stats", "themes", "rank_history", "ddragon", "replays", "insights",
    "recap", "riot_client", "theme_css", "layout", "components", "views",
    "runtime",
)


def _install_stubs():
    for name in STUBBED:
        if name in sys.modules and not isinstance(sys.modules[name], types.ModuleType):
            continue
        module = types.ModuleType(name)
        module.__getattr__ = lambda _n: _Stub()
        sys.modules.setdefault(name, module)
    sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
    sys.modules["riotwatcher"].LolWatcher = _Stub
    sys.modules["riotwatcher"].RiotWatcher = _Stub


class TestModuleImports:
    def test_every_module_imports_without_executing_anything(self):
        _install_stubs()
        failures = []
        for name in APP_MODULES:
            try:
                __import__(name)
            except Exception as exc:              # noqa: BLE001 - report, don't mask
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
        assert not failures, "modules failed to import: " + "; ".join(failures)

    def test_theme_css_defines_without_rendering(self):
        """The specific regression: theme_css must *define* the CSS builder,
        not call it. Calling it at import time is what broke startup."""
        _install_stubs()
        import theme_css

        assert callable(theme_css.render_theme_css)

    def test_ui_modules_expose_their_entry_points(self):
        _install_stubs()
        import components
        import layout
        import views

        for page in ("page_home", "page_champions", "page_trends", "page_deepdive",
                     "page_compare", "page_roles", "page_duo", "page_raw"):
            assert hasattr(views, page), f"views is missing {page}"
        for helper in ("render_hero", "section_card", "percent_table", "metric_grid"):
            assert hasattr(layout, helper), f"layout is missing {helper}"
        assert hasattr(components, "recent_games_feed")
