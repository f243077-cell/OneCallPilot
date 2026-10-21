"""One investigation, end to end (architecture §5.2–§5.6, §6.1, §6.2).

``Investigation.run()`` drives the pure state machine: it asks the LLM
provider, calls ``AgentEffects`` for every side effect, and feeds outcomes
back as events. Budgets are decided by the state machine; the wall clock is
enforced here by running the whole investigation under one timeout of
``max_seconds`` from job pickup (AI-003). On expiry, or on any terminal
escalation, ``effects.escalate`` runs outside that timeout so it is never cut
off, and the evidence gathered so far stays.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from oncallpilot.agent.citations import CitationCheck, check_citations
from oncallpilot.agent.effects import AgentEffects, EvidenceView, IncidentContext, RunMeta
from oncallpilot.agent.providers.base import (
    InvalidStructuredOutputError,
    LLMProvider,
    LLMUnavailableError,
    M,
    Msg,
    Usage,
)
from oncallpilot.agent.reflection import apply_reflection, top_confidence
from oncallpilot.agent.schemas import (
    DisproofPlanOut,
    HypothesisOut,
    HypothesisSetOut,
    ProposalOut,
    ReflectionOut,
)
from oncallpilot.agent.state_machine import (
    AgentState,
    Event,
    GatherTurn,
    HypothesesChecked,
    LLMUnavailable,
    ProposalChecked,
    ReflectionDone,
    SeedLoaded,
    StructuredAttempt,
    StructuredCall,
    Timeout,
    ToolCalled,
    transition,
)
from oncallpilot.agent.tools.registry import read_tool_specs

log = logging.getLogger(__name__)

MAX_AGENT_SECONDS = 90.0
CONFIDENCE_THRESHOLD = 0.70


class Prompts(Protocol):
    """The prompt texts of one prompt version (``agent/prompts/v{N}/``)."""

    def system(self) -> str: ...
    def gather(self) -> str: ...
    def hypothesize(self) -> str: ...
    def disproof(self) -> str: ...
    def reflect(self) -> str: ...
    def propose(self) -> str: ...


class _Stop(Exception):
    """The state machine reached a terminal phase."""


@dataclass
class Investigation:
    provider: LLMProvider
    effects: AgentEffects
    prompts: Prompts
    max_seconds: float = MAX_AGENT_SECONDS
    threshold: float = CONFIDENCE_THRESHOLD
    citation_check: Callable[[HypothesisOut, dict[str, str]], CitationCheck] = check_citations
    clock: Callable[[], float] = time.monotonic
    state: AgentState = field(default_factory=AgentState)
    messages: list[Msg] = field(default_factory=list)
    evidence: dict[str, EvidenceView] = field(default_factory=dict)
    usage: list[Usage] = field(default_factory=list)

    async def run(self) -> AgentState:
        started = self.clock()
        try:
            await asyncio.wait_for(self._investigate(), timeout=self.max_seconds)
        except TimeoutError:
            if not self.state.terminal:
                self._apply(Timeout())
        except _Stop:
            pass
        except LLMUnavailableError:
            if not self.state.terminal:
                self._apply(LLMUnavailable())
        if self.state.phase == "ESCALATED" and self.state.escalation_reason is not None:
            await self.effects.escalate(self.state.escalation_reason)
        terminal: Literal["PROPOSED", "ESCALATED"] = (
            "PROPOSED" if self.state.phase == "PROPOSED" else "ESCALATED"
        )
        await self.effects.finish(
            RunMeta(
                tool_calls=self.state.tool_calls,
                llm_calls=self.state.llm_calls,
                input_tokens=sum(u.input_tokens for u in self.usage),
                output_tokens=sum(u.output_tokens for u in self.usage),
                duration_ms=int((self.clock() - started) * 1000),
                terminal_state=terminal,
            )
        )
        log.info("investigation finished: %s", self.state.phase)
        return self.state

    # --- the phases ---------------------------------------------------------

    async def _investigate(self) -> None:
        context = await self.effects.load_context()
        self._start(context)
        await self._gather()
        hypotheses = await self._hypothesize()
        await self._reflect(hypotheses)
        await self._propose()

    def _start(self, context: IncidentContext) -> None:
        for item in context.evidence:
            self.evidence[item.ref] = item
        seed = "\n\n".join(item.rendered for item in context.evidence)
        self.messages = [
            Msg("system", self.prompts.system()),
            Msg(
                "user",
                f"{self.prompts.gather()}\n\nService: {context.service}\n"
                f"Incident: {context.title}\n\n{seed}",
            ),
        ]
        self._apply(SeedLoaded())

    async def _gather(self) -> None:
        specs = read_tool_specs()
        while self.state.can_gather:
            started = self.clock()
            turn = await self.provider.step_with_tools(self.messages, specs)
            self.usage.append(turn.usage)
            await self._record(
                "gather",
                self.messages,
                json.dumps(
                    {
                        "text": turn.text,
                        "done": turn.done,
                        "tool_calls": [
                            {"name": c.name, "arguments": c.arguments} for c in turn.tool_calls
                        ],
                    }
                ),
                turn.usage,
                started,
            )
            allowed = turn.tool_calls[: self.state.gather_calls_left]
            self.messages.append(
                Msg("assistant", turn.text, tool_calls=tuple(allowed), raw=turn.raw)
            )
            for call in allowed:
                outcome = await self.effects.run_tool(call.name, call.arguments, "gather")
                if outcome.evidence is not None:
                    self.evidence[outcome.evidence.ref] = outcome.evidence
                self.messages.append(Msg("tool", outcome.message, tool_call_id=call.id))
            done = turn.done or not turn.tool_calls
            self._apply(GatherTurn(calls=len(allowed), done=done))

    async def _hypothesize(self) -> list[HypothesisOut]:
        reply = await self._structured("hypothesize", HypothesisSetOut, self.prompts.hypothesize())
        known = set(self.evidence)
        valid = [h for h in reply.hypotheses if set(h.evidence_refs) <= known]
        valid = [h.model_copy(update={"rank": i}) for i, h in enumerate(valid, start=1)]
        self._apply(HypothesesChecked(valid=len(valid)))
        await self.effects.store_hypotheses(valid)
        return valid

    async def _reflect(self, hypotheses: list[HypothesisOut]) -> None:
        plan = await self._structured("disproof_plan", DisproofPlanOut, self.prompts.disproof())
        outcome = await self.effects.run_tool(plan.tool, plan.params, "disproof")
        self._apply(ToolCalled("disproof"))
        if outcome.evidence is not None:
            self.evidence[outcome.evidence.ref] = outcome.evidence
        self.messages.append(Msg("user", f"Disproof query result:\n{outcome.message}"))
        verdict = await self._structured("reflect", ReflectionOut, self.prompts.reflect())
        kinds: dict[str, str] = {ref: view.kind for ref, view in self.evidence.items()}
        citations = {h.rank: self.citation_check(h, kinds) for h in hypotheses}
        ranked = apply_reflection(hypotheses, verdict, citations)
        await self.effects.store_reflection(ranked)
        self._apply(ReflectionDone(top_confidence(ranked), self.threshold))

    async def _propose(self) -> None:
        proposal = await self._structured("propose", ProposalOut, self.prompts.propose())
        self._apply(ToolCalled("propose"))
        if proposal.action == "none":
            self._apply(ProposalChecked("none"))
            return
        if not set(proposal.evidence_refs) <= set(self.evidence):
            self._apply(ProposalChecked("invalid"))
            return
        self._apply(ProposalChecked(await self.effects.propose(proposal)))

    # --- helpers ------------------------------------------------------------

    async def _structured(self, call: StructuredCall, schema: type[M], prompt: str) -> M:
        """One structured call with at most one repair (AI-020).

        The prompt, any invalid reply with its repair request, and the valid
        reply stay in the conversation, as the model saw them (``Msg.raw``).
        """
        messages = [*self.messages, Msg("user", prompt)]
        while True:
            started = self.clock()
            try:
                reply = await self.provider.structured(messages, schema)
            except InvalidStructuredOutputError as exc:
                self.usage.append(exc.usage)
                await self._record(call, messages, f"INVALID: {exc.error}", exc.usage, started)
                self._apply(StructuredAttempt(call, valid=False))
                messages = [
                    *messages,
                    Msg("assistant", exc.text or "(invalid reply)", raw=exc.raw),
                    Msg(
                        "user",
                        "Your reply did not match the required JSON schema. Error:\n"
                        f"{exc.error[:2000]}\nReply again with only valid JSON.",
                    ),
                ]
                continue
            self.usage.append(reply.usage)
            await self._record(call, messages, reply.value.model_dump_json(), reply.usage, started)
            self._apply(StructuredAttempt(call, valid=True))
            self.messages = [
                *messages,
                Msg("assistant", reply.value.model_dump_json(), raw=reply.raw),
            ]
            return reply.value

    async def _record(
        self, kind: str, messages: list[Msg], response: str, usage: Usage, started: float
    ) -> None:
        prompt = "\n\n".join(f"[{m.role}] {m.content}" for m in messages)
        latency_ms = int((self.clock() - started) * 1000)
        await self.effects.record_llm_call(kind, prompt, response, usage, latency_ms)

    def _apply(self, event: Event) -> None:
        self.state = transition(self.state, event)
        if self.state.terminal and not isinstance(event, (Timeout, LLMUnavailable)):
            raise _Stop()
