# Restarting a service

## When a restart helps
A restart clears state that has gone bad inside a running process while the
code and configuration are fine:
- memory that grows steadily over hours until the process slows down, is
  killed for running out of memory, or restarts in a loop;
- a pool of connections (database, cache, HTTP) that is used up and never
  returns connections, so new work waits or times out;
- a client that lost its connection to a backing service and does not
  reconnect on its own, after that service is reachable again;
- a backing service (such as a cache) that is itself stopped or unresponsive.

## Confirming the cause
- Memory: the resident memory of the process climbs without falling back after
  load drops; restarts or out-of-memory kills appear in the container health.
- Connection pools: the in-use count sits at the pool maximum and the wait time
  for a connection grows, while the database itself answers quickly.
- A stopped dependency: the container health shows it exited or restarting, and
  the dependent service logs connection-refused or timeout errors.
- A disproof check: if the same symptom started right after a deploy, suspect
  the release before suspecting state (see "Rolling back a bad release").

## Action
Restart the affected service (`restart_service`). The runner restarts the
running containers of its active release one at a time and waits for each to
be healthy, or starts them if they have exited. Low risk, tap approval.

## When not to restart
- The service crashes at start-up with a configuration or code error: a restart
  reproduces the crash. Look at the release history instead.
- The cause is outside the service (a slow third-party dependency, a database
  that is down): restarting moves nothing and loses in-flight work.

## Verifying
The signal that triggered the incident returns to normal and stays there. If it
comes back within minutes, the cause is not transient state: escalate with the
evidence.
