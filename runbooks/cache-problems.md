# Cache problems

## Two different failures
- **The cache server is down or unreachable.** Every request that reads the
  cache fails or falls back to slow paths; logs show connection errors to the
  cache. Restart the cache service (`restart_service`) if its container has
  stopped or hangs; clearing keys cannot help when the server is unreachable.
- **The cache holds wrong data.** The cache answers, but responses are stale or
  inconsistent (old prices, items that no longer exist). Clearing that cache
  (`clear_cache`) forces fresh values to be loaded on demand.

## Confirming the cause
- Check the cache container's health and the application's cache error metric.
- Check application logs for connection errors versus data errors.
- A disproof check: if the database or the upstream that fills the cache is
  failing, clearing or restarting the cache only shifts load onto it.

## Action and risk
`clear_cache` deletes every key of one application cache through the store's
admin API; it is medium risk (a cold cache raises load on the database for a
while) and needs biometric approval. It has no undo: the cache refills from the
source of truth.

## Verifying
Cache errors stop and the affected endpoints return correct data. Watch the
database load while the cache warms up.
