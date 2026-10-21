# Rolling back a bad release

## Symptoms
Errors or latency rise shortly after a new release of a service starts serving
traffic, while request volume is normal. Failures often concentrate on one
endpoint or one kind of request, and the stack traces point at application code that changed in the
release. Restarting the service does not help: the new containers fail the
same way.

## Confirming the cause
- Compare the time the error rate first rose with the deploy history of the
  service. A release deployed within a few minutes before the rise is the prime
  suspect; one deployed hours or days earlier usually is not.
- Read the error logs of the service: exceptions in code paths the release
  touched, appearing only after the deploy, support a bad release.
- Check the other signals. If request rate, memory and dependency latency are
  all normal, a capacity or dependency problem is unlikely.
- A disproof check: if the same errors also appear before the deploy, or on the
  previous release, the release is not the cause.

## Action
Roll the service back to the release that was active before (`rollback_deploy`
with the previous release as the target). The runner starts the same number of
replicas of the target release, waits until they are healthy, then stops the
current release's replicas, so the service keeps serving throughout. This is a
medium-risk action and needs biometric approval.

## Verifying
The error rate returns to its baseline within a few minutes, and the failing
requests succeed again. If errors continue at the same level on the old
release, the release was not the cause: roll forward again and escalate.

## Undo
The rollback's own rollback step returns the service to the release it replaced.
