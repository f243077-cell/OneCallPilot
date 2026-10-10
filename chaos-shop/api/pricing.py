"""Order totals and coupons.

Release behaviour (architecture §11.3):
- 1.4.0 sums the cart lines.
- 1.5.0 ("checkout: compute totals with new pricing rounding") rounds totals up to
  a step that grows with the order size. The step lookup is off by one: orders of
  50.00 or more raise IndexError, so most checkouts fail with 500 (scenario 2).
- Both releases parse coupon codes ``NAME-PERCENT``. A code without a numeric
  suffix raises ValueError whose message repeats the code (scenario 8).
"""

from bisect import bisect_right
from collections.abc import Sequence

from api.deps import CartLine

# (orders from this many cents, round up to a multiple of this many cents)
ROUNDING_STEPS = ((0, 5), (2_000, 10), (5_000, 50))
KNOWN_COUPONS = {"WELCOME": 10, "SPRING": 15}


def release_tuple(release: str) -> tuple[int, ...]:
    return tuple(int(part) for part in release.split("."))


def _rounding_step(total_cents: int) -> int:
    bounds = [start for start, _ in ROUNDING_STEPS]
    return ROUNDING_STEPS[bisect_right(bounds, total_cents)][1]


def order_total_cents(lines: Sequence[CartLine], release: str) -> int:
    total = sum(line.quantity * line.price_cents for line in lines)
    if release_tuple(release) < (1, 5, 0):
        return total
    step = _rounding_step(total)
    return -(-total // step) * step


def coupon_percent(code: str) -> int:
    """Discount in percent for a known coupon such as ``WELCOME-10``; 0 for unknown ones."""
    name, _, percent = code.rpartition("-")
    value = int(percent)
    return value if KNOWN_COUPONS.get(name.upper()) == value else 0


def apply_coupon(total_cents: int, percent: int) -> int:
    return total_cents - total_cents * percent // 100
