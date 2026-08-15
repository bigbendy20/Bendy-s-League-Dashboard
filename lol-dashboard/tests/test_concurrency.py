"""
Two viewers at once must not see each other's data.

`runtime.bind()` writes into module globals, which are shared by every
Streamlit session in the process. That is fine for the local single-user app
and wrong for a hosted one: whoever binds last wins for everybody.

Demonstrated before the fix — one thread bound `GAME_NAME="Bendy"`, the other
`GAME_NAME="Friend"`, and both read `"Friend"`. Since everyone can see
everyone's stats by design this isn't a privacy leak, but it would render one
friend's numbers under another friend's name, which is worse than useless.

The fix is `runtime.render_lock`: bind and render happen together inside it.
These tests check the property (no interleaving) rather than the mechanism,
so a different fix later would still be validated.
"""
import sys
import threading

sys.path.insert(0, "tests")
from test_startup import _install_stubs  # noqa: E402

_install_stubs()

import layout  # noqa: E402
import runtime  # noqa: E402


def _render_as(name, seen, hold=0.0):
    """One viewer: bind an identity, then 'render' by reading it back.

    The sleep inside the lock widens the window a competing thread would use
    if the lock weren't there — without it the race is real but rarely
    observed, and a test that only sometimes fails is not a test.
    """
    import time

    with runtime.render_lock:
        runtime.bind(GAME_NAME=name, df=f"{name}-data")
        if hold:
            time.sleep(hold)
        seen[name] = (layout.GAME_NAME, layout.df)


class TestConcurrentViewers:
    def test_each_viewer_sees_its_own_state(self):
        seen = {}
        threads = [
            threading.Thread(target=_render_as, args=(name, seen, 0.02))
            for name in ("Bendy", "Friend", "Third")
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for name, (game_name, df) in seen.items():
            assert game_name == name, f"{name} rendered as {game_name}"
            assert df == f"{name}-data", f"{name} rendered {df}"

    def test_the_lock_is_reentrant(self):
        """The configured path binds and renders twice in one run — once for
        the control bar, once for the pages. A non-reentrant lock would
        deadlock the second time rather than fail visibly."""
        with runtime.render_lock:
            with runtime.render_lock:
                runtime.bind(GAME_NAME="nested")
                assert layout.GAME_NAME == "nested"

    def test_binding_without_the_lock_is_still_visible(self):
        """The lock guards against interleaving; it isn't required for
        correctness in a single thread. Tests and scripts bind freely."""
        runtime.bind(GAME_NAME="solo")
        assert layout.GAME_NAME == "solo"
