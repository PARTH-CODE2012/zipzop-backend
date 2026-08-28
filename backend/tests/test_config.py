"""What the application refuses to start with.

`assert_production_safe()` is the one function whose failure mode is *not*
booting, so it is the one place where a missing test costs a deploy rather than
a bug report. Every case here is a configuration somebody could plausibly push:
a `.env` copied from a laptop, a provider half-wired, a test key that was never
swapped.

Nothing here touches the database, but the suite's session fixture migrates one
anyway — that is the price of one conftest for the whole suite, and it is
cheaper than a second one.
"""

import pytest

from app.config import Settings, assert_production_safe


def production(**overrides: object) -> Settings:
    """A production configuration that is otherwise clean.

    Every field the guard looks at is passed explicitly. `Settings` reads
    `.env` when a field is absent, so a test that relied on defaults would pass
    or fail depending on the developer's own file — which is the kind of test
    that goes green on one machine and red on another.
    """
    base: dict[str, object] = {
        "environment": "production",
        "debug": False,
        "jwt_secret_key": "a-real-production-secret",
        "jwt_algorithm": "RS256",
        "s3_access_key_id": "AKIAREALKEY",
        "stripe_secret_key": "",
        "stripe_webhook_secret": "",
        "razorpay_key_id": "",
        "razorpay_key_secret": "",
        "razorpay_webhook_secret": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def problems_for(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Run the guard against a given configuration and return what it objected to."""
    monkeypatch.setattr("app.config.settings", settings)
    try:
        assert_production_safe()
    except RuntimeError as failure:
        return str(failure).removeprefix("unsafe production configuration: ").split("; ")
    return []


# --------------------------------------------------------------------------
# The pre-existing guards
# --------------------------------------------------------------------------


def test_a_clean_production_configuration_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    assert problems_for(production(), monkeypatch) == []


def test_development_defaults_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    found = problems_for(
        production(
            jwt_secret_key="dev-only-change-me",
            jwt_algorithm="HS256",
            debug=True,
            s3_access_key_id="zipzop",
        ),
        monkeypatch,
    )
    assert len(found) == 4


def test_nothing_is_checked_outside_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local development runs on exactly the values production refuses."""
    local = Settings(
        environment="local",
        debug=True,
        jwt_secret_key="dev-only-change-me",
        jwt_algorithm="HS256",
        s3_access_key_id="zipzop",
        razorpay_key_id="rzp_test_EXAMPLE0000000",
    )
    assert problems_for(local, monkeypatch) == []


# --------------------------------------------------------------------------
# Billing — added 25 August with the Razorpay keys
# --------------------------------------------------------------------------


def test_no_payment_provider_is_not_a_problem_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    """**The case that matters today.**

    Billing lands in M6 and these fields are empty in every environment until
    then. A guard that refused to boot over an absent payment provider would
    block a production deploy of an application that does not take payments.
    """
    assert problems_for(production(), monkeypatch) == []


def test_a_test_key_in_production_refuses_to_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure this guard exists for.

    Test keys accept a card, return a success and move no money — so a deploy
    with them looks like it works, right up until somebody asks where the
    revenue went. Nothing else in the system can tell the difference.
    """
    found = problems_for(
        production(
            razorpay_key_id="rzp_test_EXAMPLE0000000",
            razorpay_key_secret="a-secret",
            razorpay_webhook_secret="a-webhook-secret",
        ),
        monkeypatch,
    )
    assert found == ["RAZORPAY_KEY_ID is a test key (rzp_test_…) in production"]


def test_a_live_key_with_no_secret_refuses_to_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Half a provider fails when a customer is trying to pay, not at startup."""
    found = problems_for(
        production(razorpay_key_id="rzp_live_abc123", razorpay_webhook_secret="w"),
        monkeypatch,
    )
    assert found == ["RAZORPAY_KEY_ID is set but RAZORPAY_KEY_SECRET is empty"]


def test_a_live_key_with_no_webhook_secret_refuses_to_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unverified billing callback is a way to grant a plan for free.

    Webhooks are what mark a subscription paid and grant its credits. Without
    the secret there is no way to tell a real one from a forged one
    (docs/03-backend-architecture.md §8.5, docs/07-security.md §6.7).
    """
    found = problems_for(
        production(razorpay_key_id="rzp_live_abc123", razorpay_key_secret="s"),
        monkeypatch,
    )
    assert found == ["RAZORPAY_WEBHOOK_SECRET is empty — incoming webhooks could not be verified"]


def test_a_fully_configured_live_provider_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        problems_for(
            production(
                razorpay_key_id="rzp_live_abc123",
                razorpay_key_secret="s",
                razorpay_webhook_secret="w",
            ),
            monkeypatch,
        )
        == []
    )


def test_the_development_pair_names_all_three_problems(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `.env` copied straight from a laptop onto a production host.

    The key here is a made-up one. The real test id is public by design — it
    ships in the browser bundle — but pinning a live account's identifier into
    a committed test teaches the habit of pasting real values into code, and
    the assertion only ever looks at the `rzp_test_` prefix.

    All three are reported together rather than one per restart — finding out
    about a misconfiguration one deploy at a time is its own kind of outage.
    """
    found = problems_for(production(razorpay_key_id="rzp_test_EXAMPLE0000000"), monkeypatch)
    assert len(found) == 3
