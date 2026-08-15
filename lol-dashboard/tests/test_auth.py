"""
Access control.

Security code is where a test that passes for the wrong reason costs the
most, so these lean hard on the failure directions. In particular: several
check that the site *stays shut*, because every plausible bug in this module
— empty config, missing secret, a comparison that doesn't normalise — fails
in the direction of letting people in.
"""
import auth


ALLOWED = auth.parse_allowlist("bendy@example.com, friend@example.com")


class TestNormalise:
    def test_lowercases_and_trims(self):
        assert auth.normalise("  Bendy@Example.COM ") == "bendy@example.com"

    def test_none_and_empty_are_empty(self):
        assert auth.normalise(None) == ""
        assert auth.normalise("") == ""


class TestParseAllowlist:
    def test_comma_separated(self):
        assert auth.parse_allowlist("a@x.com,b@x.com") == {"a@x.com", "b@x.com"}

    def test_newline_separated(self):
        """`secrets.toml` invites a multi-line string; both forms have to
        work or the site locks everyone out for a formatting reason."""
        assert auth.parse_allowlist("a@x.com\nb@x.com") == {"a@x.com", "b@x.com"}

    def test_a_real_list(self):
        assert auth.parse_allowlist(["A@X.com", " b@x.com "]) == {"a@x.com", "b@x.com"}

    def test_entries_are_normalised(self):
        """If only the incoming email is lowercased and the list isn't, a
        capitalised entry never matches and that person can't get in."""
        assert auth.parse_allowlist("Bendy@Example.COM") == {"bendy@example.com"}

    def test_blank_entries_are_dropped(self):
        """A trailing comma would otherwise add an empty string to the set —
        which, combined with a blank email, would match."""
        assert auth.parse_allowlist("a@x.com,,  ,") == {"a@x.com"}

    def test_missing_config_is_an_empty_set(self):
        assert auth.parse_allowlist(None) == frozenset()
        assert auth.parse_allowlist("") == frozenset()


class TestIsAllowed:
    def test_a_listed_address_is_allowed(self):
        assert auth.is_allowed("bendy@example.com", ALLOWED)

    def test_case_and_whitespace_are_ignored(self):
        assert auth.is_allowed("  Bendy@Example.COM  ", ALLOWED)

    def test_an_unlisted_address_is_refused(self):
        assert not auth.is_allowed("stranger@example.com", ALLOWED)

    def test_an_empty_allowlist_denies_everyone(self):
        """The single most important assertion here. A missing or typo'd
        secret name yields an empty list, and treating that as 'allow all'
        would open the site silently — failing open, at the exact moment
        nobody is watching."""
        assert not auth.is_allowed("bendy@example.com", frozenset())
        assert not auth.is_allowed("anyone@anywhere.com", auth.parse_allowlist(None))

    def test_a_blank_email_is_refused(self):
        """Blank-matches-blank is how an anonymous visitor gets let in."""
        assert not auth.is_allowed("", ALLOWED)
        assert not auth.is_allowed(None, ALLOWED)
        assert not auth.is_allowed("   ", ALLOWED)

    def test_a_blank_email_is_refused_even_by_a_malformed_allowlist(self):
        """Defence in depth, and the version of the above that can actually
        fail.

        `parse_allowlist` strips empty entries, so against a parsed list the
        explicit blank-email guard is redundant — removing it changes
        nothing, which mutation testing duly showed. It stops being redundant
        the moment an allow-list is built some other way and contains an
        empty string, at which point a signed-out visitor with no email would
        match it. Constructing that set directly is what makes the guard
        load-bearing rather than decorative.
        """
        malformed = frozenset({"", "bendy@example.com"})
        assert not auth.is_allowed("", malformed)
        assert not auth.is_allowed(None, malformed)

    def test_a_substring_is_not_a_match(self):
        """`bendy@example.com.attacker.net` contains a listed address. Set
        membership rather than substring matching is what makes that safe,
        and it's worth pinning so nobody 'improves' it into a `in` check."""
        assert not auth.is_allowed("bendy@example.com.attacker.net", ALLOWED)
        assert not auth.is_allowed("evilbendy@example.com", ALLOWED)


class _User:
    def __init__(self, is_logged_in=False, email=""):
        self.is_logged_in = is_logged_in
        self.email = email


class TestGate:
    def test_signed_out_is_anonymous(self):
        decision = auth.gate(_User(), ALLOWED)
        assert decision["state"] == "anonymous"

    def test_auth_not_configured_is_anonymous_not_allowed(self):
        """`st.user` is None when no identity provider is set up. That must
        read as 'nobody is signed in', never as 'no checks required'."""
        assert auth.gate(None, ALLOWED)["state"] == "anonymous"

    def test_a_listed_user_is_allowed(self):
        decision = auth.gate(_User(True, "Bendy@Example.com"), ALLOWED)
        assert decision["state"] == "allowed"
        assert decision["email"] == "bendy@example.com"

    def test_an_unlisted_user_is_denied(self):
        decision = auth.gate(_User(True, "stranger@example.com"), ALLOWED)
        assert decision["state"] == "denied"

    def test_the_denial_names_the_account(self):
        """The likeliest cause is being signed into the wrong account, and
        'not on the list' without saying which account sends people round in
        circles."""
        decision = auth.gate(_User(True, "stranger@example.com"), ALLOWED)
        assert "stranger@example.com" in decision["message"]

    def test_user_facing_messages_name_no_provider(self):
        """The group is on mixed email hosts and the provider is deployment
        config, so a message that says 'Google' is wrong for whoever isn't
        using it. This caught real staleness: both messages still said Google
        after the module became provider-agnostic."""
        messages = [
            auth.gate(_User(), ALLOWED)["message"],
            auth.gate(_User(True, "stranger@example.com"), ALLOWED)["message"],
        ]
        for message in messages:
            for provider in ("Google", "Gmail", "Microsoft", "Auth0"):
                assert provider not in message, f"{provider!r} in {message!r}"

    def test_a_signed_in_user_with_no_email_is_denied(self):
        """A provider that returns a token without an email claim must not
        fall through to allowed."""
        assert auth.gate(_User(True, ""), ALLOWED)["state"] == "denied"

    def test_an_empty_allowlist_denies_a_signed_in_user(self):
        assert auth.gate(_User(True, "bendy@example.com"), frozenset())["state"] == "denied"


class TestLocalBypass:
    def test_off_by_default(self):
        """Absence of config must never mean absence of security."""
        assert not auth.local_bypass({})
        assert not auth.local_bypass({"ALLOW_ANONYMOUS": ""})

    def test_requires_an_explicit_opt_in(self):
        for value in ("1", "true", "TRUE", "yes"):
            assert auth.local_bypass({"ALLOW_ANONYMOUS": value})

    def test_other_values_do_not_enable_it(self):
        """`0` and `false` are the values someone writes when they mean off;
        a truthy-string check would enable the bypass for both."""
        for value in ("0", "false", "no", "off"):
            assert not auth.local_bypass({"ALLOW_ANONYMOUS": value})


class TestSignInOptions:
    """Which sign-in buttons to show.

    Provider choice is deployment configuration, not a code decision. The
    group turned out not to be all on one email host, and because `is_allowed`
    matches the *address* and never the issuer, adding a provider costs a
    secrets entry and a button — no access rule changes.
    """

    def test_no_providers_falls_back_to_the_default(self):
        """Streamlit's unnamed provider, configured directly under [auth].
        One button, `st.login()` with no argument."""
        assert auth.sign_in_options({}) == [auth.DEFAULT_PROVIDER]
        assert auth.sign_in_options({"auth": {}}) == [auth.DEFAULT_PROVIDER]

    def test_named_providers_each_get_a_button(self):
        secrets = {"auth": {
            "redirect_uri": "http://x/oauth2callback",
            "cookie_secret": "s",
            "google": {"client_id": "x"},
            "microsoft": {"client_id": "y"},
        }}
        assert auth.sign_in_options(secrets) == [
            ("google", "Sign in with Google"),
            ("microsoft", "Sign in with Microsoft"),
        ]

    def test_shared_settings_are_not_mistaken_for_providers(self):
        """`redirect_uri` and `cookie_secret` live under [auth] alongside the
        provider tables. Treating them as providers would render a 'Sign in
        with Cookie Secret' button and call `st.login('cookie_secret')`."""
        secrets = {"auth": {
            "redirect_uri": "http://x/oauth2callback",
            "cookie_secret": "s",
            "expose_tokens": ["id"],
            "auth0": {"client_id": "x"},
        }}
        assert auth.sign_in_options(secrets) == [("auth0", "Sign in with Auth0")]

    def test_only_nested_tables_count_as_providers(self):
        """A future scalar setting under [auth] shouldn't become a button
        just because it isn't in the known-shared list."""
        secrets = {"auth": {"some_future_flag": True, "okta": {"client_id": "x"}}}
        assert auth.sign_in_options(secrets) == [("okta", "Sign in with Okta")]

    def test_providers_are_ordered_deterministically(self):
        """Buttons that reorder between reruns are disconcerting, and
        Streamlit keys them by position."""
        secrets = {"auth": {"microsoft": {"c": 1}, "auth0": {"c": 1}, "google": {"c": 1}}}
        assert [p for p, _ in auth.sign_in_options(secrets)] == [
            "auth0", "google", "microsoft"]

    def test_a_secrets_object_that_raises_is_handled(self):
        """`st.secrets` raises rather than returning None when no secrets file
        exists — the normal case for local development. That must degrade to
        the default button, not crash the sign-in screen."""
        class Exploding:
            def get(self, key, default=None):
                raise FileNotFoundError("no secrets.toml")

        assert auth.sign_in_options(Exploding()) == [auth.DEFAULT_PROVIDER]
