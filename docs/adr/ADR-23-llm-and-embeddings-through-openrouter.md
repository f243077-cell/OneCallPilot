# ADR-23 — LLM and embeddings through OpenRouter

- **Status:** Accepted, 2026-11-02 (Tanzeel)
- **Changes:** ADR-13 (how the primary model is reached), ADR-14 (how embeddings are reached); architecture §2.3, §6.7; AI-021, AI-026
- **Code:** `backend/oncallpilot/agent/providers/openrouter.py`, `backend/oncallpilot/worker/__main__.py`; tests in `backend/tests/agent/test_openrouter.py`

## Context
ADR-13 makes a Gemini Flash-class model the primary LLM, reached with a Gemini API key, and ADR-14 takes the embeddings from the same key. The project now has an **OpenRouter** key instead of a Gemini key. OpenRouter serves the same Google models over an OpenAI-compatible API, including embeddings.

Checked against the live API on 2026-11-02, with the agent's real schemas and tools:
- `google/gemini-3.8-flash` (the newest Flash-class model listed with `tools` and `structured_outputs`) answers tool calls and strict `json_schema` replies for `HypothesisSetOut` and `ProposalOut` (free-form `params` objects included); every reply validated. 3–7 s per call.
- The model reasons before answering (about 200–600 reasoning tokens per call), and these count towards `max_tokens`. For Gemini 3 models OpenRouter returns `reasoning_details` (an encrypted thought signature per tool call) that must be sent back unchanged in the next turn.
- `POST /embeddings` with `google/gemini-embedding-001` and `dimensions: 768` returns 768 floats that are **not** unit length (norm ≈ 0.59), so ADR-14's manual L2 normalisation still applies. `input_type` is ignored: a query embedded with and without it gives the same vector (cosine 1.0), so the `RETRIEVAL_*` task types do not reach the model.
- `provider: {data_collection: "deny"}` (no provider that stores or trains on prompts) still routes both calls to Google.

## Decision
1. **New adapters behind the existing interfaces:** `OpenRouterProvider` (`LLMProvider`) and `OpenRouterEmbeddingProvider` (`EmbeddingProvider`), over the REST API with `httpx` (no new dependency). `LLM_PROVIDER=openrouter` selects both; the key is `OPENROUTER_API_KEY`, sent only in the `Authorization` header and held only by backend-worker.
2. **Primary model:** `LLM_MODEL=google/gemini-3.8-flash`. ADR-13's rule for choosing and pinning the model is unchanged (pinned in B2.13, frozen at `bench-freeze`); only the transport changes. The Claude fallback stays as ADR-13 says.
3. **Request settings, fixed in code:** temperature 0, `reasoning.effort = "low"`, `max_tokens` 4,096 for gather turns and 8,192 for structured calls (room for reasoning), `provider = {require_parameters: true, data_collection: "deny"}`, a 30 s timeout per call and one retry after 2 s on 429, 5xx or a network error (architecture §6.2). Structured calls use `response_format: json_schema` with `strict: true`; every reply is still validated with Pydantic, and an invalid one gets the single repair turn (AI-020).
4. **Replay:** each assistant message is kept exactly as OpenRouter returned it (`Msg.raw`, reasoning details included) and sent back unchanged. A tool call that the agent did not run (budget) is answered with an error tool message, so every call has a result.
5. **Embeddings follow the LLM provider:** `openrouter` uses `google/gemini-embedding-001` through OpenRouter, recorded as `embedding_model = "openrouter/google/gemini-embedding-001"`; `gemini` and `anthropic` keep the direct Gemini embeddings (ADR-14). Runbook chunks whose stored `embedding_model` differs from the current provider's are embedded again at start-up, so vectors from two models never mix in one search.
6. The direct Gemini adapter stays and keeps working with `LLM_PROVIDER=gemini`.

## Consequences
- One OpenRouter key runs both the agent and runbook search. The key is billed per token (the probe calls cost well under one cent).
- Runbook search loses the retrieval task types. Similarity still ranks the right runbook first in the probe (0.76 vs 0.57 for an unrelated one); the regression suite and the Gate G2 run show whether it is enough.
- Routing goes through a third party. Only redacted, truncated evidence reaches prompts anyway (§6.4, SEC-007), and `data_collection: "deny"` keeps prompts away from providers that store them.
- Reasoning tokens make calls slower and larger than a non-reasoning model would; `effort = "low"` keeps them within the 30 s per-call and 90 s per-investigation budgets.
- The runner's isolation test also refuses `OPENROUTER_API_KEY` (§7.3: the runner never receives an LLM key).
- C1 `AgentMeta.provider` gained `openrouter` (contracts 0.2.0): the first live smoke showed `GET /incidents/{id}` failing with 500 because the value did not validate.
