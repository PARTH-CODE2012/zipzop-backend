"""Billing period arithmetic.

One function, in its own module, because two very different callers need the
same answer and neither should own it: registration sets the first period, and
the renewal sweep sets every one after that. They were the same calculation
living in `repositories/user.py` until M6 needed it too — and a private helper
imported across packages is a copy waiting to happen.
"""

import calendar
from datetime import datetime


def add_a_month(moment: datetime) -> datetime:
    """The renewal boundary.

    Calendar months, not 30 days: someone who signs up on the 31st renews on the
    28th, 30th or 31st as the next month allows, rather than drifting a day
    earlier every month for a year. Over a year that drift is twelve days, which
    is a customer being charged thirteen times for twelve months.
    """
    year = moment.year + (moment.month // 12)
    month = moment.month % 12 + 1
    day = min(moment.day, _days_in_month(year, month))
    return moment.replace(year=year, month=month, day=day)


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]
