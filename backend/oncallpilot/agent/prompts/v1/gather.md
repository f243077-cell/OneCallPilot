An alert opened this incident. The detector's signal is evidence E1 below.

Investigate with the read-only tools. Each call returns new evidence with its own reference. You have at most 6 tool calls, so choose each one to separate plausible causes:
- what changed recently (deploys, rollbacks, scaling, resets);
- which service and which containers are affected, and since when;
- what the errors and logs say (narrow log searches with `contains` and a level);
- whether a dependency (database, cache, payment provider) is slow, full or failing;
- whether resources (memory, CPU, connections) are exhausted or traffic is unusual.

Call tools until you can name the likely root cause, then reply without any tool call. Do not repeat a query that already answered the question.
