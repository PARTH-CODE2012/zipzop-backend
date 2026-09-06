"""Which currency to suggest, and which provider takes it.

Two decisions, kept apart on purpose. **What the user pays in** is their choice
and we only guess a default; **who processes it** is not a choice at all
(docs/03-backend-architecture.md §8.2).
"""

from typing import Final

from app.config import settings
from app.models import PaymentProvider

#: Every currency we can price in. `plans` carries a column per member, which is
#: why this is a pair and not a list — a third currency is a schema change, not
#: a config change, and it should feel like one.
INR: Final = "INR"
USD: Final = "USD"
SUPPORTED_CURRENCIES: Final[tuple[str, ...]] = (INR, USD)

#: Country codes billed in rupees. One entry, and the list exists so the second
#: one is an edit rather than an `if`.
_INR_COUNTRIES: Final[frozenset[str]] = frozenset({"IN"})

#: Headers an edge network sets with the caller's country.
#:
#: **There is no GeoIP database in this deployment**, and there should not be:
#: a database that has to be refreshed monthly to keep a *suggestion* accurate
#: is a maintenance burden out of all proportion to what it buys. Every CDN in
#: front of a service like this already resolves the country; behind none of
#: them the fallback below applies, which is exactly right for local
#: development.
_COUNTRY_HEADERS: Final[tuple[str, ...]] = (
    "cf-ipcountry",  # Cloudflare
    "x-vercel-ip-country",
    "x-appengine-country",
    "x-country-code",  # several load balancers, and our own tests
)


def country_from_headers(headers: dict[str, str]) -> str | None:
    """The caller's country, if something in front of us worked it out.

    Case-insensitive, because header case is guaranteed by nothing, and
    `XX`/`T1` are discarded — Cloudflare uses them for "unknown" and for Tor,
    and treating either as a country would suggest a currency at random.
    """
    lowered = {k.lower(): v for k, v in headers.items()}
    for name in _COUNTRY_HEADERS:
        value = lowered.get(name)
        if value and len(value.strip()) == 2:
            code = value.strip().upper()
            if code not in {"XX", "T1"}:
                return code
    return None


def suggest_currency(headers: dict[str, str]) -> str:
    """A default for the pricing page. **A suggestion, never a lock.**

    Contract §7 is explicit that the client must let the user change it: VPNs,
    travellers and expatriates make IP unreliable, and someone in London paying
    with an Indian card has to be able to choose rupees. This function exists to
    save most people a click, not to decide anything.
    """
    country = country_from_headers(headers)
    if country in _INR_COUNTRIES:
        return INR
    return USD


def normalise_currency(requested: str | None, headers: dict[str, str]) -> str | None:
    """The currency to actually use. `None` when the caller asked for one we
    cannot price in — the route turns that into a 422 naming what we do take."""
    if requested is None:
        return suggest_currency(headers)
    code = requested.strip().upper()
    return code if code in SUPPORTED_CURRENCIES else None


def provider_for_currency(currency: str) -> PaymentProvider:
    """Who processes this. Derived, never chosen by the client (§8.2).

    Rupees go to Razorpay because that is the market it exists for. Dollars are
    the open question: §8.2's destination is Stripe, and **Stripe is deferred,
    not dropped** (docs/13-mvp-direction.md §5) — so until that adapter exists
    the setting below sends them to Razorpay, which is what "Razorpay first"
    means in practice.

    Whether Razorpay may actually charge USD is an *account activation* matter
    and not an API capability. It is one of the two things blocking M6 from
    being finished rather than written (docs/20-m6-readiness.md §3.2), and if
    the answer comes back no, the fix is one environment variable here plus the
    Stripe adapter — not a reshaping of anything.
    """
    if currency.upper() == INR:
        return PaymentProvider.RAZORPAY
    return PaymentProvider(settings.billing_provider_for_usd)
