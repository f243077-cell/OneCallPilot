# Slow or failing third-party dependencies

## Symptoms
Latency of requests that call an external dependency (a payment provider, an
external API) rises, often with timeouts on exactly those requests. Requests
that do not touch the dependency stay fast. The dependency's own response time,
measured from our side, is high.

## Confirming the cause
- Compare upstream latency for the dependency with overall latency: if they
  rise together and only dependent endpoints are slow, the dependency is the
  cause.
- Check whether anything of ours changed (deploys, traffic). If not, the
  problem is outside our control.

## What to do
None of our automated actions fixes a third party: restarting, scaling or
rolling back our services does not make the provider faster, and scaling adds
more waiting requests. Escalate to a human with the evidence, so they can
contact the provider, enable a fallback, or communicate the degradation.

## Verifying
The upstream latency returns to normal on its own once the provider recovers;
our services need no change.
