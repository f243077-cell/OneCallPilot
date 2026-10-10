"""C5 — Runner messages (architecture §5.6, §5.9, §5.10, §5.12, §9.5; RUN-001…RUN-018, FR-024).

Transport
    Redis Streams on copilot-redis. Each stream entry has exactly one field,
    ``msg``, whose value is the message as compact JSON (UTF-8).

    ============================ ========== ======================================
    stream                       group      messages
    ============================ ========== ======================================
    ``ocp:runner:requests``      ``runner`` ``dry_run`` (backend-worker) and
                                            ``execute`` (backend-api only)
    ``ocp:runner:results``       ``backend`` every result from the runner
    ============================ ========== ======================================

    Every message carries ``sig``; see ``signing.py`` for the algorithm and
    which key signs which message.

Requests
    ``dry_run`` asks for a preview (RUN-007) and changes nothing. Its
    ``execution_id``, ``idempotency_key``, ``approved_by`` and
    ``approved_at`` are ``null``.

    ``execute`` is sent only by the approve handler, after the approval and
    the execution rows are committed (§5.9). It must carry ``execution_id``,
    ``proposal_id``, the dry run's ``state_fingerprint``, the approval's
    ``idempotency_key``, ``approved_by`` and ``approved_at`` (SEC-002), and
    ``expires_at = approved_at + 60 s``.

    ``action`` and ``params`` are only shape-checked here, on purpose: the
    runner checks them against ``contracts/actions.yaml`` (C4), so an unknown
    or disabled action is refused with ``ACTION_NOT_ALLOWED`` (RUN-003) and a
    value outside the catalogue with ``VALIDATION_ERROR`` (RUN-018).

Runner checks, in order (§5.10)
    A failed check produces a ``refused`` result with the code shown, and
    nothing changes.

    1. signature, with the key for the message type → ``BAD_SIGNATURE``
    2. message shape (this model) → ``VALIDATION_ERROR``
    3. ``expires_at`` not yet passed → ``REQUEST_EXPIRED``
    4. ``catalogue_version`` equals the version of the catalogue the runner
       loaded → ``VALIDATION_ERROR`` (fail closed: a proposal built from
       another catalogue is never acted on)
    5. action enabled in the catalogue, with a handler → ``ACTION_NOT_ALLOWED``
    6. parameters valid against the catalogue → ``VALIDATION_ERROR``
    7. targets allowlisted in ``runner/targets.yaml`` → ``TARGET_NOT_ALLOWED``
    8. ``execute`` only: ``SET NX ocp:runner:idem:{execution_id}`` (a
       duplicate is acknowledged and ignored, with no result); rate limit →
       ``RATE_LIMITED``; cooldown → ``COOLDOWN_ACTIVE``; then the state
       fingerprint, where a mismatch gives ``aborted`` with ``STATE_DRIFT``.

    **Every final answer to an ``execute`` claims its ID first.** Before it
    sends any ``refused`` or ``aborted`` result for an ``execute`` (checks 1–7
    included), the runner sets ``ocp:runner:idem:{execution_id}`` with
    ``SET NX``; if the key already exists, the message is a duplicate and gets
    no result. So a forged message that borrows a real ``execution_id`` ends
    that execution as refused, and the genuine ``execute`` arriving later is
    a duplicate that never runs: nothing executes, which matches the
    incident's ``escalated`` (``runner_refused``) state.

    **A message that cannot be answered gets no result.** If a message has no
    usable ``type``, no UUID ``request_id``, or (for ``execute``) no UUID
    ``execution_id``, no valid ``RunnerResult`` can be built. The runner logs
    it, acknowledges it, and sends nothing; for an ``execute``, the worker's
    sweeper ends the execution with ``RUNNER_TIMEOUT`` (FR-024).

Results
    ``dry_run_result``
        ``ok`` with ``dry_run``, or ``refused`` with ``error_code``.
    ``progress``
        ``ok`` with ``step``, one per step of an execution (RUN-013).
    ``execution_result``
        The final status of an ``execute``:

        - ``succeeded``: carries ``health_after.structural`` (RUN-014).
        - ``failed``: a step or the structural check failed.
          ``health_after`` is present if the check ran.
        - ``aborted``: carries ``error_code``, ``STATE_DRIFT`` when the
          fingerprint changed.
        - ``refused``: carries ``error_code``.

        ``succeeded`` and ``failed`` also carry ``captured``: the values that
        the action's ``captures`` names in the catalogue (``previous_replicas``,
        ``previous_release``). The worker builds the rollback step from them
        (C4 ``rollback_step``, FR-015).
"""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from oncallpilot_contracts.common import ContractModel, Semver, Sha256Hex, UtcDatetime
from oncallpilot_contracts.domain import DryRunResult, ParamName, ParamValue, StructuralHealth
from oncallpilot_contracts.enums import RunnerErrorCode, StepStatus

REQUESTS_STREAM = "ocp:runner:requests"
REQUESTS_GROUP = "runner"
RESULTS_STREAM = "ocp:runner:results"
RESULTS_GROUP = "backend"
STREAM_FIELD = "msg"
EXECUTE_TTL_SECONDS = 60

ActionName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Signature = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

APPROVAL_FIELDS = ("execution_id", "idempotency_key", "approved_by", "approved_at")


class RunnerRequest(ContractModel):
    """A signed request on ``ocp:runner:requests`` (§9.5)."""

    type: Literal["dry_run", "execute"]
    request_id: UUID
    execution_id: UUID | None
    proposal_id: UUID | None
    action: ActionName
    params: dict[ParamName, ParamValue]
    state_fingerprint: Sha256Hex | None
    idempotency_key: UUID | None
    approved_by: UUID | None
    approved_at: UtcDatetime | None
    issued_at: UtcDatetime
    expires_at: UtcDatetime
    catalogue_version: Semver
    sig: Signature

    @model_validator(mode="after")
    def _fields_match_type(self) -> Self:
        if self.type == "execute":
            missing = [
                name
                for name in (*APPROVAL_FIELDS, "proposal_id", "state_fingerprint")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(f"execute needs {', '.join(missing)}")
        else:
            present = [name for name in APPROVAL_FIELDS if getattr(self, name) is not None]
            if present:
                raise ValueError(f"dry_run must not carry {', '.join(present)}")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        return self


class RunnerStep(ContractModel):
    """One execution step reported in a ``progress`` result (RUN-013)."""

    name: str = Field(min_length=1, max_length=100)
    status: StepStatus
    message: str = Field(max_length=300)


class RunnerHealthAfter(ContractModel):
    structural: StructuralHealth


class RunnerResult(ContractModel):
    """A signed result on ``ocp:runner:results`` (§9.5)."""

    type: Literal["dry_run_result", "progress", "execution_result"]
    request_id: UUID
    execution_id: UUID | None
    status: Literal["ok", "refused", "succeeded", "failed", "aborted"]
    error_code: RunnerErrorCode | None
    dry_run: DryRunResult | None
    step: RunnerStep | None
    health_after: RunnerHealthAfter | None
    captured: dict[ParamName, ParamValue] | None
    ts: UtcDatetime
    sig: Signature

    @model_validator(mode="after")
    def _fields_match_type(self) -> Self:
        if self.type == "dry_run_result":
            self._check_dry_run_result()
        elif self.type == "progress":
            self._check_progress()
        else:
            self._check_execution_result()
        return self

    def _only(self, *allowed: str) -> None:
        extra = [
            name
            for name in ("dry_run", "step", "health_after", "captured")
            if name not in allowed and getattr(self, name) is not None
        ]
        if extra:
            raise ValueError(f"a {self.type} result must not carry {', '.join(extra)}")

    def _check_dry_run_result(self) -> None:
        if self.execution_id is not None:
            raise ValueError("a dry_run_result has no execution_id")
        if self.status == "ok":
            if self.dry_run is None or self.error_code is not None:
                raise ValueError("an ok dry_run_result carries dry_run and no error_code")
        elif self.status == "refused":
            if self.error_code is None or self.dry_run is not None:
                raise ValueError("a refused dry_run_result carries error_code and no dry_run")
        else:
            raise ValueError("a dry_run_result is ok or refused")
        self._only("dry_run")

    def _check_progress(self) -> None:
        if self.execution_id is None or self.step is None:
            raise ValueError("a progress result carries execution_id and step")
        if self.status != "ok" or self.error_code is not None:
            raise ValueError("a progress result has status ok and no error_code")
        self._only("step")

    def _check_execution_result(self) -> None:
        if self.execution_id is None:
            raise ValueError("an execution_result carries execution_id")
        if self.status == "ok":
            raise ValueError("an execution_result is succeeded, failed, aborted or refused")
        if self.status == "succeeded":
            if self.error_code is not None:
                raise ValueError("a succeeded execution_result has no error_code")
            if self.health_after is None or not self.health_after.structural.passed:
                raise ValueError("a succeeded execution_result carries a passed structural check")
        if self.status in ("aborted", "refused"):
            if self.error_code is None:
                raise ValueError(f"an {self.status} execution_result carries error_code")
            self._only()
        else:
            self._only("health_after", "captured")
