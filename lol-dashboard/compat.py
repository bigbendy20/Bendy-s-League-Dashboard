"""
Small shims for APIs that changed under us.

Two deprecations turned up in the deployed logs, both already past their
stated removal date, both firing on nearly every widget:

    use_container_width will be removed after 2025-12-31
    Timestamp.utcnow is deprecated

Neither is urgent on its own. Together they produced hundreds of lines per
page load, which is worse than noise — a log that is 95% deprecation warnings
is a log nobody reads, and the next real error arrives in the middle of it.

**Detected rather than assumed.** Streamlit's docs say to use
`width="stretch"` but don't say which version introduced it, and this project
has already been burned once by asserting a library's behaviour from a
general rule. So the signature is inspected: if `width` is accepted, it's
used; if not, the old keyword is. That keeps a friend on an older Streamlit
working instead of trading a warning for a TypeError.
"""
import inspect

import pandas as pd


def _supports_width() -> bool:
    """Does the installed Streamlit take `width=` on data widgets?

    Wrapped in a try because `streamlit` is stubbed in the startup tests, and
    a shim that explodes on a stub would take the whole suite with it.
    Defaults to the modern spelling: on a real, current Streamlit that's
    right, and the fallback only matters for old installs.
    """
    try:
        import streamlit as st

        return "width" in inspect.signature(st.dataframe).parameters
    except Exception:
        return True


# Spread into any call that used `use_container_width=True`:
#     st.dataframe(frame, **FULL_WIDTH)
FULL_WIDTH = {"width": "stretch"} if _supports_width() else {"use_container_width": True}


def utcnow() -> pd.Timestamp:
    """`pd.Timestamp.utcnow()`, which pandas 4 deprecates.

    Returns a **tz-aware** UTC timestamp, which is what `utcnow()` has always
    returned. Worth stating because the first version of this shim asserted
    the opposite — that `utcnow()` was naive — stripped the timezone, and
    broke two rank-history tests that compare against a tz-aware column.

    Both spellings were then run side by side and printed:

        pd.Timestamp.utcnow()   -> 2026-08-15 05:39:22+0000  tz=UTC
        pd.Timestamp.now("UTC") -> 2026-08-15 05:39:22+0000  tz=UTC

    Identical. So this is a rename with no behaviour change, which is the
    only kind of shim worth having — and the ten seconds of checking is what
    turns that from a hope into a fact.
    """
    return pd.Timestamp.now("UTC")
