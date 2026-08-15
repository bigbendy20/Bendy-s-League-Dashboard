"""
Static check that the UI modules' injected globals actually exist.

`app.py` binds runtime state into `layout`, `components` and `views` (see
runtime.py). The cost of that approach is that those files reference names
they never import, so nothing local would catch a typo or a value that
app.py forgot to pass — it'd surface as a NameError halfway down a page,
which is exactly the kind of bug the Streamlit-free test suite can't reach.

This walks each module's AST, collects every global name it *reads*, and
subtracts: builtins, names defined or imported in that module, and the set
`app.py` actually binds (parsed out of the `runtime.bind(...)` call rather
than hardcoded, so the two can't drift). Anything left over is a real
problem and exits non-zero.

Run:  python tools/check_bindings.py
"""
import ast
import builtins
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Modules that receive injected runtime state — checked against the bound set.
CHECKED = ("layout.py", "components.py", "views.py")

# Modules that get NOTHING injected and must resolve every name themselves.
# `theme_css` is pure by design: all its inputs are arguments. It was
# initially left out of the checks entirely, which let a reference to
# `HEADER_FONT` — a constant that stayed behind in app.py during the split —
# survive all the way to a runtime NameError. Anything self-contained
# belongs here, not in CHECKED, because allowing the bound names would hide
# exactly this bug.
SELF_CONTAINED = ("theme_css.py",)
# Always present at runtime in any module; not something app.py binds.
MODULE_DUNDERS = {"__file__", "__name__", "__doc__", "__package__", "__spec__"}

# Modules that must contain *definitions only*. They're imported by app.py
# at startup, before any runtime state is bound and before Streamlit is set
# up, so any statement that actually executes at import time will blow up.
#
# This check exists because that's exactly what happened: extracting these
# modules by line range dragged a trailing `st.markdown(render_theme_css(…))`
# call into theme_css.py, which then raised NameError on import. py_compile
# and the binding check both passed, because neither looks at whether module
# level code *runs*.
IMPORT_SAFE = ("theme_css.py", "layout.py", "components.py", "views.py")


def bound_by_app() -> set:
    """Every name app.py makes available to the UI modules.

    Two shapes are collected. Direct `runtime.bind(x=..., y=...)` calls, and
    `SOMETHING_BINDING = dict(x=..., y=...)` assignments — the latter because
    binding and rendering now have to happen together inside a lock, so the
    values are assembled into a dict first and bound in one call at the point
    of render (see runtime.py).

    Collecting both matters: renaming the calls to `dict()` made the bound
    set invisible to this checker, which promptly failed with 40-odd unbound
    names. That's the checker working, but it's worth naming the shape here
    so the next restructure doesn't quietly slip past it instead.
    """
    tree = ast.parse((ROOT / "app.py").read_text())
    names = set()
    binding_dicts = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "dict"
        and any(isinstance(t, ast.Name) and t.id.endswith("_BINDING")
                for t in node.targets)
    ]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_bind = (
            (isinstance(func, ast.Attribute) and func.attr == "bind")
            or (isinstance(func, ast.Name) and func.id == "bind")
        )
        if not is_bind and node not in binding_dicts:
            continue
        for kw in node.keywords:
            if kw.arg:
                names.add(kw.arg)
            else:
                # **{...} splat — the components re-export. Resolve it by
                # taking every public render-ish name the module defines.
                names.update(_component_exports())
    return names


def _component_exports() -> set:
    tree = ast.parse((ROOT / "components.py").read_text())
    prefixes = ("render_", "recent_games_feed", "champion_card_grid",
                "pretty_trend_chart", "sparkline")
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith(prefixes)
    }


def dir_on_bound_module() -> list:
    """app.py must not build bind() kwargs from dir() of a bound module.

    bind() writes into those modules, so on Streamlit's next rerun dir() also
    returns everything the previous rerun injected — including names app.py
    passes explicitly, which then arrive twice. That shipped as
    "bind() got multiple values for keyword argument 'render_hero'": fine on
    first load, broken on the first click. components.py freezes its own
    EXPORTS tuple at import time instead.
    """
    tree = ast.parse((ROOT / "app.py").read_text())
    bound_modules = {m.removesuffix(".py") for m in CHECKED}
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "dir" and node.args):
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Name) and arg.id in bound_modules:
            bad.append((node.lineno, arg.id))
    return bad


def module_local_names(tree: ast.AST) -> set:
    """Everything defined, imported or assigned at any level in a module."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            names.update(a.arg for a in getattr(node.args, "args", []))
            names.update(a.arg for a in getattr(node.args, "kwonlyargs", []))
            if getattr(node.args, "vararg", None):
                names.add(node.args.vararg.arg)
            if getattr(node.args, "kwarg", None):
                names.add(node.args.kwarg.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.comprehension,)):
            for t in ast.walk(node.target):
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def read_names(tree: ast.AST) -> set:
    return {
        node.id for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def import_time_statements(tree: ast.AST) -> list:
    """Module-level nodes that actually execute on import.

    Definitions, imports, docstrings, constants and `if __name__` guards are
    fine. A bare call — `st.markdown(...)` — is not: these modules are
    imported before Streamlit is configured and before any state is bound.
    """
    offenders = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign,
                             ast.Pass)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # docstring
        if isinstance(node, ast.If):
            continue  # `if __name__ == "__main__"` / import fallbacks
        if isinstance(node, ast.Try):
            continue  # optional-import guards
        offenders.append((node.lineno, type(node).__name__))
    return offenders


# Inside `page_deepdive`, these must be fed the role-scoped frame, never the
# page-wide one. Runes, summoner spells, builds, skins and matchups all differ
# by role; handing any of them `filtered_df` silently re-pools the roles and
# produces a table that looks fine and describes nothing.
#
# This is a static check because it has to be. The scoping is done by
# filtering the frame rather than by a `role=` argument, so a regression here
# changes no function signature, raises nothing, and returns a perfectly
# well-formed DataFrame — the failure is invisible to every unit test.
ROLE_SCOPED_CALLS = {
    "matchup_win_rate": {"scoped_df"},
    "skin_usage": {"scoped_df"},
    "build_win_rate": {"scoped_df"},
    "render_keystone_win_rates": {"sub", "rune_df"},
    "render_summoner_combo_win_rates": {"sub", "rune_df"},
}


def role_scope_violations() -> list:
    """Role-dependent calls in views.py handed an unscoped frame."""
    tree = ast.parse((ROOT / "views.py").read_text())
    bad = []
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef) or func.name != "page_deepdive":
            continue
        for node in ast.walk(func):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            allowed = ROLE_SCOPED_CALLS.get(node.func.id)
            if not allowed or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Name) and first.id not in allowed:
                bad.append((node.lineno, node.func.id, first.id,
                            "/".join(sorted(allowed))))
    return bad


def lock_violations() -> list:
    """Binding and rendering in app.py must happen inside `render_lock`.

    Module globals are shared by every Streamlit session in the process, so a
    bind that isn't paired with its render under the lock lets a concurrent
    viewer rebind between the two — and that viewer's profile is what gets
    drawn. Demonstrated with two threads: both read the identity of whichever
    bound last.

    Static rather than a unit test because the property is about *where the
    calls sit in app.py*, not about what any function returns. Moving
    `pg.run()` one line down, outside the `with`, changes no signature, raises
    nothing, and reintroduces the bug in full.

    Two rules:
      1. every `runtime.bind(...)` and `pg.run()` sits inside a
         `with runtime.render_lock:` block;
      2. the bind that renders the pages passes *every* `*_BINDING` dict —
         binding only the later one leaves the earlier values as whatever
         another session happened to write.
    """
    tree = ast.parse((ROOT / "app.py").read_text())
    guarded = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            ctx = item.context_expr
            if isinstance(ctx, ast.Attribute) and ctx.attr == "render_lock":
                guarded.append((node.lineno, node.end_lineno))

    def inside_lock(node) -> bool:
        return any(start <= node.lineno <= end for start, end in guarded)

    binding_dicts = {
        t.id for node in ast.walk(tree) if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name) and t.id.endswith("_BINDING")
    }

    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_bind = isinstance(func, ast.Attribute) and func.attr == "bind"
        is_render = isinstance(func, ast.Attribute) and func.attr == "run"
        if not (is_bind or is_render):
            continue
        if not inside_lock(node):
            what = "runtime.bind()" if is_bind else "pg.run()"
            bad.append((node.lineno, f"{what} outside `with runtime.render_lock:`"))
        elif is_bind and node.keywords:
            referenced = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            missing = binding_dicts - referenced
            # The onboarding bind legitimately uses only the base set; only
            # flag a bind that omits a dict defined *before* it.
            missing = {m for m in missing
                       if any(a.lineno < node.lineno for a in ast.walk(tree)
                              if isinstance(a, ast.Assign)
                              and any(isinstance(t, ast.Name) and t.id == m
                                      for t in a.targets))}
            if missing and len(binding_dicts) > 1 and node.lineno > max(
                a.lineno for a in ast.walk(tree) if isinstance(a, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id in binding_dicts
                        for t in a.targets)
            ):
                bad.append((node.lineno,
                            f"bind omits {', '.join(sorted(missing))} — earlier "
                            f"values may belong to another session"))
    return bad


def main() -> int:
    bound = bound_by_app()
    # `from stats import *` is in every bound module; take its public surface.
    import importlib.util
    import sys

    # `stats` imports its siblings, so the project root has to be importable.
    # This worked for as long as stats.py imported nothing local; the first
    # sibling import broke the checker rather than the app, which is a
    # confusing way to find out.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("stats", ROOT / "stats.py")
    stats_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stats_mod)
    star = {n for n in dir(stats_mod) if not n.startswith("_")}

    failures = []
    # Bound modules: injected state counts as available.
    for fname in CHECKED:
        tree = ast.parse((ROOT / fname).read_text())
        unknown = (
            read_names(tree)
            - module_local_names(tree)
            - set(dir(builtins))
            - bound
            - star
            - MODULE_DUNDERS
        )
        if unknown:
            failures.append((fname, [f"unbound names -> {', '.join(sorted(unknown))}"]))

    # Self-contained modules: nothing is injected, so `bound` is deliberately
    # NOT subtracted. Allowing it here is what previously hid HEADER_FONT.
    for fname in SELF_CONTAINED:
        tree = ast.parse((ROOT / fname).read_text())
        unknown = (
            read_names(tree)
            - module_local_names(tree)
            - set(dir(builtins))
            - MODULE_DUNDERS
        )
        if unknown:
            failures.append(
                (fname, [f"must be self-contained but uses -> {', '.join(sorted(unknown))}"])
            )

    # Second check: nothing in these modules may run at import time.
    for fname in IMPORT_SAFE:
        tree = ast.parse((ROOT / fname).read_text())
        offenders = import_time_statements(tree)
        if offenders:
            where = ", ".join(f"line {ln} ({kind})" for ln, kind in offenders)
            failures.append((fname, [f"executes at import: {where}"]))

    # Fifth check: bind and render together, under the lock.
    for lineno, problem in lock_violations():
        failures.append(("app.py", [f"line {lineno}: {problem}"]))

    # Fourth check: role-dependent stats must get the role-scoped frame.
    for lineno, call, got, want in role_scope_violations():
        failures.append(
            ("views.py", [f"line {lineno}: {call}({got}) in page_deepdive — "
                          f"expected {want}; an unscoped frame silently re-pools roles"])
        )

    # Third check: no dir()-of-a-bound-module in app.py (rerun pollution).
    for lineno, mod in dir_on_bound_module():
        failures.append(
            ("app.py", [f"line {lineno}: dir({mod}) — bound modules accumulate "
                        f"injected names across reruns; use a frozen export list"])
        )

    if failures:
        for fname, names in failures:
            print(f"FAIL {fname}: {', '.join(names)}")
        return 1

    print(
        f"OK — {len(CHECKED)} bound + {len(SELF_CONTAINED)} self-contained modules, "
        f"{len(bound)} bound names, all references resolve; "
        f"{len(IMPORT_SAFE)} modules import-safe; "
        f"{len(ROLE_SCOPED_CALLS)} role-scoped call sites verified; bind/render lock pairing verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
