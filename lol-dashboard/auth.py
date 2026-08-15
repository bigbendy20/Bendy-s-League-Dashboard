"""
Who is allowed in, and who they are.

Two separate questions, and conflating them is the classic way to build an
open door. *Authentication* — proving you control an email address — is
handled by Streamlit's `st.login()` OIDC flow, against whichever providers
are configured. *Authorisation* — whether that address is one of ours — is
decided here, against an explicit allow-list.

**Why an allow-list rather than "any signed-in account".** Signing in proves
someone controls an email address; billions of people do. Without the second
check the site would be public with extra steps. Riot's personal-key terms
permit "a small private community", and an explicit list is what makes that
claim true rather than aspirational.

**Nothing here knows or cares which provider issued the identity.** That was
not foresight so much as the natural consequence of separating the two
questions — but it paid off: when the group turned out to be on mixed email
hosts, adding providers was a secrets change with no code change at all.

**Everything here is a pure function over strings.** No Streamlit import, no
network, no database — so the rules can be tested exhaustively, and the
Streamlit-facing code is left with nothing to decide.

Emails are compared case-insensitively and whitespace-trimmed throughout.
Identity providers are inconsistent about both, and a mismatch here doesn't
error, it just quietly locks out a legitimate user or — worse, depending on
the direction — fails to lock out someone.
"""
from collections.abc import Mapping


def normalise(email) -> str:
    """The one canonical form. Used on both sides of every comparison.

    A single helper rather than `.strip().lower()` scattered around: the bug
    this prevents is applying it to one side of a comparison and not the
    other, which looks correct at every individual call site.
    """
    return (email or "").strip().lower()


def parse_allowlist(raw) -> frozenset:
    """Config value -> a set of permitted emails.

    Accepts a comma- or newline-separated string, or an actual list, because
    this arrives from `secrets.toml` where either is natural to write and
    getting it wrong should not silently produce an empty (locked) or
    single-blob (never-matching) list.
    """
    if raw is None:
        return frozenset()
    if isinstance(raw, str):
        parts = raw.replace("\n", ",").split(",")
    else:
        parts = list(raw)
    return frozenset(filter(None, (normalise(p) for p in parts)))


def is_allowed(email, allowlist) -> bool:
    """Is this signed-in account one of ours?

    An empty allow-list denies everyone. That's deliberate and it is the
    important decision in this module: the alternative — treating "no list
    configured" as "allow all" — turns a missing config value or a typo'd
    secret name into a wide-open site, and it would fail *open*, silently, at
    exactly the moment nobody is looking.
    """
    email = normalise(email)
    if not email or not allowlist:
        return False
    return email in allowlist


# Fallback when no providers are named in secrets: Streamlit's unnamed
# default provider, configured directly under `[auth]`.
DEFAULT_PROVIDER = (None, "Sign in")


def _is_table(value) -> bool:
    """Is this a nested TOML table — i.e. a configured provider?

    Deliberately not `isinstance(value, dict)`. `st.secrets` hands back
    `AttrDict`, Streamlit's own mapping type, which is **not** a `dict`
    subclass. So the dict check was false for every real provider while being
    true for every test fixture, which passed plain dicts. The result on the
    deployed site: no providers found, the unnamed fallback used, and
    `st.login()` called with nothing to log in *to* — a StreamlitAuthError on
    the first click, in production, having passed locally.

    Duck-typing on the mapping protocol instead. Anything that behaves like a
    table is one, whichever library produced it — which is the property that
    was actually meant all along.
    """
    # The string guard is redundant today — a `str` has `__getitem__` but no
    # `keys`, so it fails the second test anyway, and mutation testing duly
    # showed that deleting this line changes nothing. It's kept because it
    # stops being redundant the moment that `and` becomes an `or`, and the
    # failure it prevents is a "Sign in with Cookie Secret" button next to the
    # real one. Cheap insurance on a line nobody will reread.
    if isinstance(value, str):
        return False
    return isinstance(value, Mapping) or (
        hasattr(value, "keys") and hasattr(value, "__getitem__"))


def sign_in_options(secrets) -> list:
    """[(provider_name, button_label)] for the sign-in screen.

    Streamlit supports several named OIDC providers at once — `[auth.google]`,
    `[auth.microsoft]`, `[auth.auth0]` and so on — and `st.login(name)` picks
    between them. Which ones exist is deployment configuration, not a code
    decision, so this reads them rather than hardcoding a provider.

    That matters more than it first appeared: the group turned out not to be
    all on Gmail. Because `is_allowed` matches on the email address and never
    on the issuer, supporting a second provider costs a secrets entry and a
    button — no change to any access rule.

    Names are derived from the `[auth.*]` sub-tables. Anything under `[auth]`
    itself (redirect_uri, cookie_secret, expose_tokens) is shared config, not
    a provider, so it's excluded by only treating nested tables as providers.
    """
    shared = {"redirect_uri", "cookie_secret", "expose_tokens"}
    auth_config = {}
    try:
        auth_config = dict(secrets.get("auth") or {})
    except Exception:
        # `st.secrets` raises rather than returning None when no secrets file
        # exists at all, which is the normal case for local development.
        return [DEFAULT_PROVIDER]

    providers = [
        name for name, value in auth_config.items()
        if name not in shared and _is_table(value)
    ]
    if not providers:
        return [DEFAULT_PROVIDER]
    return [(name, f"Sign in with {name.replace('-', ' ').title()}")
            for name in sorted(providers)]


def gate(user, allowlist) -> dict:
    """Decide what to do with the current visitor.

    `user` is `st.user`-shaped: something with `is_logged_in` and `email`,
    or None when auth isn't configured at all. Returns a dict with a
    `state` of "anonymous", "denied" or "allowed" and a message for the two
    that need one.

    Returning a decision rather than rendering anything keeps this testable
    and keeps the branching out of the page code.
    """
    if user is None or not getattr(user, "is_logged_in", False):
        return {
            "state": "anonymous",
            "email": "",
            "message": "Sign in to view the board.",
        }

    email = normalise(getattr(user, "email", ""))
    if not is_allowed(email, allowlist):
        # Naming the address is deliberate: the overwhelmingly likely cause
        # is signing in with the wrong account, and "not on the list" without
        # saying *which* account sends people round in circles. Deliberately
        # provider-neutral — the same message has to make sense whether they
        # came in via Google, Microsoft or anything else.
        return {
            "state": "denied",
            "email": email,
            "message": (
                f"{email} isn't on the invite list. If you have more than one "
                "account, check you're signed in with the right one."
            ),
        }
    return {"state": "allowed", "email": email, "message": ""}


def local_bypass(env) -> bool:
    """Whether to skip auth entirely — local development only.

    Guarded by an explicit `ALLOW_ANONYMOUS=1`, not by "is an allow-list
    configured", because absence of config must never mean absence of
    security. Someone deploying with a missing secret gets a locked site and
    an obvious problem, not an open one.
    """
    return str(env.get("ALLOW_ANONYMOUS", "")).strip().lower() in {"1", "true", "yes"}
