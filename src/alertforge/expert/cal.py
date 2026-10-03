"""A task's calendar (R6): the day the queue is handed over, when things happened before it, and the numbers
incidents get.

- `today` is a weekday at the end of September; every incident, deploy and split is before it.
- INC numbers and PagerDuty incident numbers are functions of the time an incident was opened, so they grow with
  time in every file of a task (one counter per task, never two independent draws).
- Release tags come from the day of the deploy. Deploys happen on working days in office hours, apart from the odd
  evening hotfix.
- Relative wording ("this week", "on Tuesday", "last Thursday") comes from the gap between a date and `today`.
"""

from __future__ import annotations

import datetime as dt
import random

UTC = dt.timezone.utc
EPOCH = dt.datetime(2026, 8, 1, tzinfo=UTC)
FIRST_DAY, LAST_DAY = dt.date(2026, 9, 22), dt.date(2026, 10, 2)
INC_HOURS = 7.0     # about three and a half incidents a day across the company
PD_MINUTES = 7.0    # PagerDuty numbers every incident on the account, about 200 a day


def ordinal(n: int) -> str:
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


class Cal:
    def __init__(self, rng: random.Random, utc_offset: float):
        days = [FIRST_DAY + dt.timedelta(d) for d in range((LAST_DAY - FIRST_DAY).days + 1)]
        self.today = rng.choice([d for d in days if d.weekday() < 5])
        self.off = utc_offset
        self.inc_base = rng.randint(2600, 3900)
        self.pd_base = rng.randint(14000, 52000)
        self.used_inc: set[int] = set()

    # ------------------------------------------------------------------ dates
    def past(self, rng: random.Random, lo: int, hi: int, weekday: bool = False) -> dt.date:
        """A day `lo`..`hi` days before today (a working day when `weekday`)."""
        for _ in range(50):
            d = self.today - dt.timedelta(days=rng.randint(lo, hi))
            if not weekday or d.weekday() < 5:
                return d
        return self.today - dt.timedelta(days=lo)

    def week_days(self, rng: random.Random, k: int, this_week: bool | None = None) -> list[dt.date]:
        """`k` distinct working days in one week before today: this week when there are enough days left in it
        (or when asked), otherwise last week."""
        mon = self.today - dt.timedelta(days=self.today.weekday())
        cur = [mon + dt.timedelta(d) for d in range(5) if mon + dt.timedelta(d) < self.today]
        last = [mon - dt.timedelta(days=7 - d) for d in range(5)]
        pool = cur if (this_week is not False and len(cur) >= k and (this_week or rng.random() < 0.6)) else last
        return sorted(rng.sample(pool, min(k, len(pool))))

    def rel(self, d: dt.date) -> str:
        """How someone writing today refers to `d`."""
        gap = (self.today - d).days
        if gap == 0:
            return "today"
        if gap == 1:
            return "yesterday"
        mon = self.today - dt.timedelta(days=self.today.weekday())
        if d >= mon:
            return f"on {d:%A}"
        if d >= mon - dt.timedelta(days=7):
            return f"last {d:%A}"
        return f"on {d:%a} {d.day} {d:%b}"

    def span(self, dates: list[dt.date]) -> str:
        """`this week`, `last week`, or `since <day>` for a set of days."""
        mon = self.today - dt.timedelta(days=self.today.weekday())
        if all(d >= mon for d in dates):
            return "this week"
        if all(mon - dt.timedelta(days=7) <= d < mon for d in dates):
            return "last week"
        return f"since {min(dates):%a} {min(dates).day} {min(dates):%b}"

    def next_weekday(self, name: str) -> str:
        """`tomorrow` / `on Thursday` (later this week) / `on Monday` (the coming one) / `next Tuesday`."""
        t = self.today.weekday()
        wd = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].index(name)
        ahead = (wd - t) % 7 or 7
        if ahead == 1:
            return "tomorrow"
        if ahead <= 6 - t or (wd == 0 and t != 0):
            return f"on {name}"
        return f"next {name}"

    def ahead(self, k: int) -> dt.date:
        """The working day `k` working days after today."""
        d = self.today
        while k:
            d += dt.timedelta(days=1)
            k -= d.weekday() < 5
        return d

    def ordinal(self, d: dt.date) -> str:
        return f"the {ordinal(d.day)}"

    @staticmethod
    def iso(d: dt.date) -> str:
        return d.strftime("%Y-%m-%d")

    # ------------------------------------------------------------------ deploys
    @staticmethod
    def version(d: dt.date, n: int) -> str:
        """Release tag cut on the day of the deploy (`v2026.09.08-2`)."""
        return f"v{d:%Y.%m.%d}-{n}"

    @staticmethod
    def office_start(rng: random.Random, hotfix: bool = False) -> str:
        """A rollout start in office hours, or an evening hotfix."""
        if hotfix:
            return f"{rng.choice([19, 20, 21])}:{rng.choice(['05', '10', '25', '40'])}"
        return f"{rng.randint(9, 16):02d}:{rng.choice(['00', '05', '15', '20', '30', '35', '45', '50'])}"

    # ------------------------------------------------------------------ numbers
    def _utc(self, d: dt.date | str, hhmm: str = "00:00", minutes: float = 0) -> dt.datetime:
        if isinstance(d, str):
            d = dt.date.fromisoformat(d)
        h, m = (int(x) for x in hhmm.split(":"))
        local = dt.datetime(d.year, d.month, d.day, h, m) + dt.timedelta(minutes=minutes)
        return (local - dt.timedelta(hours=self.off)).replace(tzinfo=UTC)

    def inc(self, d: dt.date | str, hhmm: str = "00:00", minutes: float = 0) -> str:
        """`INC-NNNN` for an incident opened at local time `d hhmm` (+`minutes`): later incidents get higher numbers."""
        t = self._utc(d, hhmm, minutes)
        n = self.inc_base + int((t - EPOCH).total_seconds() / 3600 / INC_HOURS)
        while n in self.used_inc:
            n += 1
        self.used_inc.add(n)
        return f"INC-{n}"

    def pd(self, iso_utc: str) -> int:
        """PagerDuty incident number for an incident created at `iso_utc` (`2026-09-08T21:52:00Z`)."""
        t = dt.datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        return self.pd_base + int((t - EPOCH).total_seconds() / 60 / PD_MINUTES)

    def number_rows(self, rows: list[dict]) -> list[dict]:
        """Rows of one export in creation order, numbered by creation time and strictly increasing."""
        rows = sorted(rows, key=lambda r: r["created_on"])
        last = 0
        for r in rows:
            n = max(self.pd(r["created_on"]), last + 1)
            r["incident_number"] = n
            last = n
        return rows

    def now_utc(self) -> str:
        """Nine in the morning on the day the queue is handed over."""
        return self._utc(self.today, "09:00").strftime("%Y-%m-%dT%H:%M:%SZ")
