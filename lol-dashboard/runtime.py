"""
Shares per-rerun state between `app.py` and the UI modules.

Why this exists: Streamlit re-runs the entire script on every interaction,
and the UI modules need a lot of common state — the loaded match DataFrame,
the queue-filtered view, the resolved accent colours, the Riot client, the
Data Dragon version, and so on. Roughly two dozen values, used across four
modules and forty-odd functions.

Two ways to do that. Thread every value through every signature, which makes
`render_highlight_reel(data, version, accent, accent_text, plot_template, …)`
out of what used to be `render_highlight_reel(data)`; or bind them into the
module namespaces once per rerun, which is what this does.

**The concurrency problem, and why the fix is a lock.**

`bind()` writes into module globals, which is correct for exactly one
concurrent user. Streamlit runs each browser session's script in its own
thread, but all sessions share one Python process and therefore one set of
module objects. Two people viewing at once would write into the same globals
and whoever bound last would win for *both*.

That isn't theoretical. Demonstrated with two threads: one bound
`GAME_NAME="Bendy"`, the other `GAME_NAME="Friend"`, and both then read
`"Friend"`. On a shared site that means one friend's page rendering another
friend's stats under their own name.

Three fixes were considered.

*Thread-local values behind a module `__getattr__` (PEP 562)* — the elegant
one, and it does not work. Module `__getattr__` is consulted for attribute
access (`layout.df`), not for bare global lookups inside that module's own
functions, which is how every one of these values is actually read.
`LOAD_GLOBAL` bypasses it entirely. Tried, and the startup simulation caught
it immediately.

*Rewrite the UI modules to read from an explicit context object* — the
genuinely correct design, and the one to reach for if this app ever grows
past a handful of users. It means touching 61 names across ~2,100 lines of
rendering code, which is a large mechanical change to make on the way to
something else.

*Serialise binding and rendering behind a lock* — what's implemented. Bind
and render happen together inside `render_lock`, so no session can observe
another's state. The cost is that page renders are serialised across
viewers. For seven friends and sub-second renders that is free; it would not
be at a hundred. Crucially it is only safe once fetching is off the request
path — a multi-hour backfill inside the lock would block everyone — which is
why the background refresher is the next piece of work and not optional.

"""
import threading

# Every module that receives the runtime binding. Kept explicit so the
# binding checker and the binder can't drift apart.
BOUND_MODULES = ("layout", "components", "views")

# Held across bind-then-render so a concurrent session cannot rebind the
# module globals mid-render. Re-entrant because the configured path binds and
# renders twice in one run (onboarding, then pages).
render_lock = threading.RLock()


def bind(**values) -> None:
    """Inject `values` as module-level globals into each UI module.

    Must be called inside `render_lock` by anything that then renders — see
    the module docstring. `bound_state()` exists so the caller can assemble
    the full set once and rebind it atomically rather than relying on values
    left over from an earlier bind in the same run, which another session may
    since have overwritten.
    """
    import importlib

    for name in BOUND_MODULES:
        module = importlib.import_module(name)
        module.__dict__.update(values)
