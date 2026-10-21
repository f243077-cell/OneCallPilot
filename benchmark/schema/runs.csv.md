# `benchmark/results/runs.csv` — one row per benchmark run (BENCH-007, C10)

Written by `bench run` (with `bench mark` for observer marks), read by `bench report`.
Rows are only ever appended; nobody edits the file by hand. `bench check` validates it.

- **Encoding:** UTF-8, comma-separated, header row first, columns in exactly this order.
- **Times:** UTC ISO-8601 with `Z` (`2026-11-02T10:04:31Z`); empty when the event did not happen.
- **Booleans:** `true` / `false`; empty when not applicable (for example `action_correct` on an escalation).

## Run identity
| Column | Meaning |
|---|---|
| `run_id` | `R<seed>-<nn>` from the plan (`bench plan`) |
| `plan_seed` | seed of the plan the run belongs to |
| `scenario_id` | 1–8 (`benchmark/scenarios.yaml`) |
| `scenario_name` | e.g. `bad-deploy` |
| `mode` | `manual` or `copilot` |
| `rep` | repetition, 1–3 |
| `operator` | who handled the incident |
| `observer` | who injected and marked; never the operator (BENCH-003) |

## Configuration
| Column | Meaning |
|---|---|
| `git_sha` | commit the stack ran |
| `llm_provider` | `gemini`, `anthropic` or empty in manual mode |
| `llm_model` | exact model ID |
| `prompt_version` | e.g. `v1` |
| `detector_config_version` | from the incident |
| `catalogue_version` | `contracts/actions.yaml` version |

## Timestamps
| Column | Meaning |
|---|---|
| `t_inject` | `chaos inject` returned |
| `t_alert` | the run's incident was opened (checked against `benchmark_run_id`) |
| `t_diagnosis` | the operator stated the root cause aloud (`bench mark diagnosis`), both modes |
| `t_proposal` | copilot: the proposal was created; manual: empty |
| `t_decision` | copilot: approved or rejected; manual: the operator started the fix |
| `t_recovered` | the incident was resolved |

## Outcome
| Column | Meaning |
|---|---|
| `diagnosis_text` | what the operator said, as marked |
| `diagnosis_correct` | the diagnosis names an accepted category / keyword of the scenario |
| `top1_correct` | copilot: the top hypothesis's category is accepted |
| `top3_correct` | copilot: any active hypothesis's category is accepted |
| `proposal_action` | e.g. `rollback_deploy`, empty if none |
| `proposal_params` | JSON object of the proposal's parameters |
| `expected_action` | `action(k=v, …)` or `escalate` (`bench scenarios`) |
| `action_correct` | the action taken matches `expected_action` |
| `escalated` | the incident was escalated |
| `unsafe_action` | an action outside the expected one was executed (must stay `false`) |
| `evidence_citations_total` | citations across the hypotheses |
| `evidence_citations_valid` | of those, citations of stored evidence |
| `timed_out` | 20 minutes passed without recovery or escalation |
| `notes` | free text, e.g. `invalid_run`, Grafana use in copilot mode |
