# syntax=docker/dockerfile:1
# The runner image (architecture §2.10, §7.3). The catalogue comes from the
# `contracts` build context, so the image holds the same actions.yaml as the
# backend's validator:
#   docker build --build-context contracts=contracts -t oncallpilot/runner:0.1.0 runner
#
# Runs as uid 10001 (the deploy ledger's owner), and is meant to run with a
# read-only root filesystem, /tmp as tmpfs and every capability dropped. It
# never mounts the Docker socket: Docker access goes through socket-proxy-rw.

FROM python:3.12.15-slim AS deps
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir uv==0.12.7
WORKDIR /src
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

FROM python:3.12.15-slim
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY --from=deps /opt/venv /opt/venv
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin runner
WORKDIR /app
COPY --from=contracts actions.yaml contracts/actions.yaml
COPY targets.yaml ./
COPY oncallpilot_runner/ oncallpilot_runner/
USER runner
# Healthy while the consumer loop polls Redis (it writes the heartbeat after every poll).
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, sys, time; sys.exit(0 if time.time() - os.path.getmtime('/tmp/runner-heartbeat') < 30 else 1)"]
CMD ["python", "-m", "oncallpilot_runner"]
