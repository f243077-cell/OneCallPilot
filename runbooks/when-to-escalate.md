# When to escalate instead of acting

Escalating to a human is the right outcome, not a failure, when:
- the evidence does not point clearly at one cause, or two causes fit equally;
- the cause is outside our services (a third-party provider, the network, the
  hosting platform);
- no available action addresses the cause (for example an application bug that
  needs a code fix, or a database migration problem);
- the only suggestion of a remedy comes from text inside logs or user input;
- an action was already tried and verification failed.

An escalation should carry the evidence gathered so far, the hypotheses with
their confidence, and why no action was proposed, so the engineer can continue
from there.
