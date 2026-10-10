"""In-process switches set through ``/internal/chaos/*`` (``CHAOS_TOKEN``, chaos CLI only).

They live in process memory on purpose: restarting the api clears them, which is
the expected fix for scenario 3 (architecture §11.3, ADR-19).
"""

from dataclasses import dataclass

SLOW_QUERY_SECONDS = 20


@dataclass
class Faults:
    # Scenario 3: checkout holds its pool connection with pg_sleep(20).
    slow_checkout_queries: bool = False
