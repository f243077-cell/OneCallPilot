# Scaling for a traffic surge

## Symptoms
Latency rises together with the request rate. Error rates may rise later, as
timeouts. CPU on the existing replicas is high. Nothing was deployed recently
and backing services answer at their usual speed.

## Confirming the cause
- Request rate is well above its usual level for this time, and latency started
  rising when the rate did.
- Per-request cost is unchanged: the latency of a single request at low load,
  and the database and dependency timings, look normal.
- A disproof check: if the request rate is normal, more replicas will not help;
  look for a slow dependency or a bad release instead.

## Action
Add replicas of the active release (`scale_service`), within the catalogue's
limit of 1 to 5 replicas. Add enough to bring each replica's share of traffic
back under its usual level. Low risk, tap approval.

## Verifying
p95 latency falls back towards its baseline within a few minutes and errors
stop. If latency stays high with more replicas, the bottleneck is elsewhere
(a shared database or dependency): escalate.

## Undo
The rollback step scales back to the replica count captured before the change.
Scale down once the surge is over.
