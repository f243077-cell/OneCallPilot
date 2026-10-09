# C7 — Telemetry contract

> **Contract:** C7 · **Drafter:** Tanzeel · **Reader:** Usman · **Version:** tracked by `contracts/VERSION`
> **Source:** `docs/architecture.md` §2.8, §6.3, §6.8, §11.1, §11.2 · **Requirements:** TB-002, TB-003, TB-004, TB-010, TB-012
> **Emitters:** Chaos Shop (`chaos-shop/`). **Consumers:** Prometheus, Alloy, Loki, Grafana (`infrastructure/observability/`), the detector, and the `query_metrics`, `query_logs`, and `get_service_health` tools.
>
> Changing any name, label, label value set, or bucket list below is a contract change (`CLAUDE.md` §8.4).

---

## 1. Docker labels

Every **managed** Chaos Shop container carries all five labels. Values are strings.

| Label | Values | Notes |
|---|---|---|
| `oncallpilot.managed` | `true` | Required on every managed container. The runner target allowlist (`runner/targets.yaml`) also requires it. |
| `oncallpilot.service` | `api`, `worker`, `payments`, `lb`, `redis`, `postgres` | Logical service name. Equals the `service` metric label, the `service` log field, and the Loki `service` label. |
| `oncallpilot.release` | Release version, e.g. `1.4.0`, `1.5.0`, `2.1.0`, `2.2.0` | For Chaos Shop code (`api`, `worker`, `payments`). For third-party images (`lb`, `redis`, `postgres`) it is the version of the pinned image tag. |
| `oncallpilot.replica` | `1` … `5` | Slot number. `api` slots use `1`–`5`; every other service uses `1`. |
| `oncallpilot.logs` | `true` | Alloy collects logs **only** from containers with this label (TB-012). |

Managed containers (TB-002, architecture §11.1):

| Container | `service` | `release` | `replica` |
|---|---|---|---|
| `cs-api-140-1` … `cs-api-140-5` | `api` | `1.4.0` | `1`–`5` |
| `cs-api-150-1` … `cs-api-150-5` | `api` | `1.5.0` | `1`–`5` |
| `cs-worker-210` | `worker` | `2.1.0` | `1` |
| `cs-worker-220` | `worker` | `2.2.0` | `1` |
| `cs-payments` | `payments` | Chaos Shop payments release | `1` |
| `cs-lb` | `lb` | nginx image version | `1` |
| `cs-redis` | `redis` | redis image version | `1` |
| `cs-postgres` | `postgres` | postgres image version | `1` |

- **`cs-loadgen` carries no `oncallpilot.*` labels.** It plays the outside world (customers), like the chaos CLI. It is not monitored, not shipped to Loki, not visible to `get_service_health`, and never a runner target. The chaos CLI finds it by container name.
- **Neutral naming (TB-010):** no label, release number, or container name may contain `bad`, `leak`, `broken`, `chaos`, `fault`, `inject`, or any other word that names a fault. Release numbers are plain semver.

Network aliases (architecture §2.8, §11.1): every `api` slot has the alias `api-upstream` (port `8000`), every `worker` slot has `worker-upstream` (port `8001`), on `chaos_net`.

---

## 2. Metrics

### 2.1 Scrape endpoints

| Service | Endpoint | Scraped by |
|---|---|---|
| `api` | `GET /metrics` on port `8000` | Prometheus `dns_sd_configs` on `api-upstream:8000` |
| `worker` | `GET /metrics` on port `8001` | Prometheus `dns_sd_configs` on `worker-upstream:8001` |

- Prometheus text exposition format, produced by the Python `prometheus_client` library.
- Only running containers resolve through the alias, so stopped slots are never scraped.
- `payments`, `lb`, `redis`, and `postgres` expose no metrics in v1. Their state reaches the copilot through logs and `get_service_health`.

### 2.2 Base labels (on every metric, including `process_*`)

| Label | Value |
|---|---|
| `service` | `oncallpilot.service` of the container (`api` or `worker`) |
| `release` | `oncallpilot.release` of the container |
| `instance` | The container name, e.g. `cs-api-140-1` |

> **Scrape config requirement (consumer side):** DNS service discovery sets the target label `instance` to `<ip>:<port>`, which would clash with the `instance` label above and rename it `exported_instance`. The Prometheus jobs for `api-upstream` and `worker-upstream` **must set `honor_labels: true`**, so the container name wins. Every query, dashboard, and tool template uses `instance` as the container name.

### 2.3 Metric list

`H` = histogram, `C` = counter, `G` = gauge. Extra labels are in addition to the base labels.

| Metric | Type | Extra labels | Emitted by | Meaning |
|---|---|---|---|---|
| `http_requests_total` | C | `route`, `method`, `status` | api | Completed HTTP requests |
| `http_request_duration_seconds` | H | `route`, `method` | api | Request latency, from first byte in to last byte out |
| `db_pool_connections` | G | `state` | api | Store-database pool connections by state |
| `db_pool_wait_seconds` | H | — | api | Time spent waiting to acquire a pool connection |
| `db_pool_timeouts_total` | C | — | api | Pool acquisitions that timed out |
| `cache_operations_total` | C | `cache`, `result` | api | Cache reads against `cs-redis` |
| `upstream_request_duration_seconds` | H | `upstream` | api | Latency of calls to upstream dependencies |
| `worker_jobs_total` | C | `type`, `result` | worker | Finished background jobs |
| `worker_job_duration_seconds` | H | `type` | worker | Background job duration |
| `app_info` | G (always `1`) | `commit` | api, worker | Build information |
| `process_resident_memory_bytes` | G | — | api, worker | Default `prometheus_client` process collector |
| `process_cpu_seconds_total` | C | — | api, worker | Default process collector |
| `process_start_time_seconds` | G | — | api, worker | Default process collector; a change means the process restarted |

The other default `process_*` series (`process_virtual_memory_bytes`, `process_open_fds`, `process_max_fds`) may be present; no consumer depends on them. Python `python_gc_*` and `python_info` series may also be present and are not part of the contract.

### 2.4 Label values

| Label | Allowed values |
|---|---|
| `route` | The route **template**, never the raw path: `/products`, `/cart`, `/checkout`, `/orders`, `/healthz` (architecture §11.1). Any request that matches no route uses `other`. `/metrics` and `/internal/*` are **not** counted. |
| `method` | Upper-case HTTP method: `GET`, `POST`, `PUT`, `DELETE`. Anything else uses `OTHER`. |
| `status` | The numeric status code as a string, e.g. `200`, `404`, `500`, `503`. |
| `state` (`db_pool_connections`) | `in_use`, `idle` |
| `cache` | `catalog`, `pricing` |
| `result` (`cache_operations_total`) | `hit`, `miss`, `error` (`error` = the cache could not be reached or the command failed) |
| `upstream` | `payments` |
| `type` (`worker_*`) | `fulfil_order`, `send_receipt`, `refresh_catalog` |
| `result` (`worker_jobs_total`) | `success`, `error` |
| `commit` | First 12 characters of the release's 40-character lower-case hex `commit_sha` (C6 deploy ledger, `chaos-shop/releases.yaml`) |

New label values need a contract change, because tool templates and the detector depend on bounded cardinality.

### 2.5 Histogram buckets (seconds)

Fixed, so `histogram_quantile` results are comparable across releases and runs.

| Metric | Buckets |
|---|---|
| `http_request_duration_seconds` | `0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30` |
| `upstream_request_duration_seconds` | `0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30` |
| `db_pool_wait_seconds` | `0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 2.5, 5, 10, 30` |
| `worker_job_duration_seconds` | `0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60` |

`+Inf` is implicit. Buckets reach 30 s because the database-pool fault holds connections for 20 s and the slow-dependency fault adds 2.5 s.

### 2.6 What each consumer relies on (informative)

| Consumer | Uses |
|---|---|
| Detector `error_rate` | `http_requests_total{service="api",status=~"5.."}` share over 1 min |
| Detector `p95_latency` | `histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{service="api"}[1m])))` |
| Detector `job_failure_rate` | `worker_jobs_total{result="error"}` share |
| `query_metrics` templates | `error_rate`, `p95_latency`, `request_rate` → `http_*`; `memory_rss` → `process_resident_memory_bytes`; `cpu_usage` → `process_cpu_seconds_total`; `process_restarts` → `process_start_time_seconds`; `db_pool_in_use` → `db_pool_connections{state="in_use"}`; `db_pool_wait_p95` → `db_pool_wait_seconds`; `cache_errors` → `cache_operations_total{result="error"}`; `upstream_latency_p95` → `upstream_request_duration_seconds`; `job_failure_rate`, `job_latency_p95` → `worker_*` |

The exact PromQL lives with the consumers (`backend/`); this table only shows which metrics they need.

---

## 3. Logs

### 3.1 Format

- **One JSON object per line on stdout** (TB-004). No multi-line output: a stack trace is a single JSON string with embedded `\n`.
- UTF-8. A line is at most 16 KiB; longer `msg` or `stacktrace` values are truncated by the emitter and end with `…[truncated]`.
- Keys that do not apply to a line are **omitted** (not `null`). Consumers treat a missing key as absent.

### 3.2 Fields

| Field | Type | Required | Value |
|---|---|---|---|
| `ts` | string | always | RFC 3339 UTC with milliseconds and `Z`, e.g. `2026-10-14T09:30:12.345Z` |
| `level` | string | always | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` (upper case) |
| `service` | string | always | Same as `oncallpilot.service` |
| `release` | string | always | Same as `oncallpilot.release` |
| `instance` | string | always | Container name, e.g. `cs-api-150-2` |
| `logger` | string | always | Logger name: `shop.<area>` for Chaos Shop code, e.g. `shop.checkout`; nginx uses `nginx.access`. Neutral (TB-010): never names a fault or the injector |
| `msg` | string | always | Human-readable message |
| `request_id` | string | request lines | 32-character lower-case hex; taken from `X-Request-ID` if valid, else generated. `cs-lb` forwards it. |
| `route` | string | request lines | Route template, same values as the `route` metric label |
| `status` | integer | request lines | HTTP status code |
| `duration_ms` | number | request lines | Request duration in milliseconds |
| `exc_type` | string | exception lines | Exception class name, e.g. `KeyError` |
| `exc_message` | string | exception lines | `str(exception)` |
| `stacktrace` | string | exception lines | Full Python traceback text, **starting with `Traceback (most recent call last):`** (the detector's `stack_traces` signal matches the literal `Traceback`) |

- Exceptions are logged at `ERROR` (or `CRITICAL` for a crash on startup) and always include `exc_type`, `exc_message`, and `stacktrace`.
- `api` writes one access line per request (`logger` = `shop.access`), at `INFO` for status < 500 and `ERROR` for ≥ 500.
- Services may add other keys. Consumers must ignore unknown keys; no consumer may depend on a key not listed above.

### 3.3 Per-service sources

| Service | Format |
|---|---|
| `api`, `worker`, `payments` | Chaos Shop JSON logger, all fields as above |
| `lb` | nginx access log in JSON (`log_format … escape=json`) with `ts`, `level` (`INFO` for status < 500, `ERROR` otherwise), `service`, `release`, `instance`, `logger` = `nginx.access`, `msg` (`"<method> <uri> <status>"`), `request_id`, `status`, `duration_ms`. The nginx **error** log is plain text. |
| `redis`, `postgres` | The images' native **plain-text** logs, unchanged |

**Non-JSON lines** (nginx error log, redis, postgres) are still shipped. The consumer (Alloy) gives them the Loki label `level="UNKNOWN"`, so `query_logs` finds them with `level=ANY` only.

### 3.4 Loki labels (attached by Alloy)

| Label | Source |
|---|---|
| `service` | Docker label `oncallpilot.service` |
| `release` | Docker label `oncallpilot.release` |
| `container` | Container name (same value as the `instance` log field) |
| `level` | The `level` JSON field; `UNKNOWN` when the line is not JSON or has no `level` |

No other field becomes a Loki label (bounded cardinality). `request_id`, `route`, `status`, and the exception fields stay in the line body.

### 3.5 Untrusted content

User-controlled request data (for example coupon codes) may appear in `msg` and `exc_message`. This is intentional (scenario 8). Every consumer treats log text as **untrusted data** and never as instructions (`CLAUDE.md` §2.6).

Chaos Shop never logs environment values, passwords, tokens, `Authorization` headers, or `CHAOS_TOKEN` / `RUNNER_ADMIN_TOKEN`.
