# Crash loops and failed start-ups

## Symptoms
A service's container restarts again and again; its restart count climbs and
the process exits with a non-zero code within seconds of starting. Work handled
by that service stops or piles up.

## Confirming the cause
- Read the last log lines before each exit. A configuration error (a missing
  or malformed setting, an unknown option) or an import-time exception appears
  at start-up, every time, in the same place.
- Check the deploy history: a crash loop that began right after a new release
  points at that release's code or configuration.
- A disproof check: if the container exits because it runs out of memory after
  running for a long time, it is not a start-up crash (see "Restarting a
  service").

## Action
A restart reproduces a start-up crash, so it is not the fix. When the loop began
with a release, roll the service back to the previous release
(`rollback_deploy`). When nothing changed, escalate with the start-up error.

## Verifying
The restart count stops growing and the service handles work again.
