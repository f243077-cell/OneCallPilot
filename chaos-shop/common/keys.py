"""cs-redis keys shared by cs-api and cs-worker (testbed only; not copilot-redis)."""

CATALOG_KEY = "cs:cache:catalog"
PRICING_KEY = "cs:cache:pricing"
JOB_QUEUE_KEY = "cs:queue:jobs"

CATALOG_TTL_SECONDS = 60
PRICING_TTL_SECONDS = 300

JOB_TYPES = ("fulfil_order", "send_receipt", "refresh_catalog")
