"""Record a referral violation and apply the lead's policy to it.

There is no admin interface, and a violation is judged by a person, so this is
how the decision is entered:

    python -m app.scripts.promo_violation CODE --kind self_use --user buyer@example.com
    python -m app.scripts.promo_violation CODE --kind shared_outside_server --note "posted in r/…"

A dry run by default. It does everything inside a transaction, prints the
outcome, and rolls it back. `--yes` commits.

The policy (6 October 2026, docs/24-m7-closure.md §2.6): the first violation
cancels the commission it earned and is a warning, the second removes the code.
Telling the owner is still a person's job. The printed outcome says what to tell
them.
"""

import argparse
import asyncio
import sys

from app.db import worker_session
from app.models import PromoViolationAction, PromoViolationKind
from app.repositories.user import UserRepository
from app.services.promo_policy import REMOVE_CODE_AT, record_violation


async def main(
    code: str, kind: PromoViolationKind, email: str | None, note: str | None, commit: bool
) -> int:
    async with worker_session() as session:
        referred = None
        if email:
            referred = await UserRepository(session).by_email(email)
            if referred is None:
                print(f"no account with the address {email!r}", file=sys.stderr)
                return 2
        try:
            outcome = await record_violation(
                session, code=code, kind=kind, referred_user=referred, note=note
            )
        except LookupError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        print(f"code            {outcome.code}")
        print(f"violation       #{outcome.number} ({kind.value})")
        if outcome.reversed_minor:
            for currency, amount in sorted(outcome.reversed_minor.items()):
                print(f"commission      {amount / 100:.2f} {currency} cancelled")
        else:
            print("commission      nothing to cancel")
        if outcome.attribution_removed:
            print("attribution     the account no longer earns this code commission")
        if outcome.action is PromoViolationAction.CODE_REMOVED:
            print("action          CODE REMOVED: it grants nothing and earns nothing from now on")
            print("tell the owner  their referral code has been removed (second violation)")
        else:
            print(f"action          WARNING ({outcome.number} of {REMOVE_CODE_AT - 1} allowed)")
            print("tell the owner  this is a warning; the next violation removes their code")

        if commit:
            await session.commit()
            print("\ncommitted")
        else:
            await session.rollback()
            print("\ndry run: nothing was saved. Run again with --yes to apply it.")
    return 0


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("code", help="the promo code, as the owner shares it")
    parser.add_argument(
        "--kind",
        required=True,
        choices=[k.value for k in PromoViolationKind],
        help="self_use: the owner used it; shared_outside_server: it was posted elsewhere",
    )
    parser.add_argument("--user", metavar="EMAIL", help="the account that came in through it")
    parser.add_argument("--note", help="what was seen, and where, for the record")
    parser.add_argument("--yes", action="store_true", help="commit (default: dry run)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse(sys.argv[1:])
    sys.exit(
        asyncio.run(main(args.code, PromoViolationKind(args.kind), args.user, args.note, args.yes))
    )
