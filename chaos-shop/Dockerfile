# syntax=docker/dockerfile:1
# Chaos Shop images, one target per service (architecture §11.1, §12.1):
#   docker build --target api --build-arg CS_RELEASE=1.4.0 -t chaos-shop/api:1.4.0 chaos-shop
# The release is baked into the image. api and worker look up their commit in
# releases.yaml at start-up (C7 app_info{commit}).

FROM python:3.12.15-slim AS deps
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir uv==0.12.7
WORKDIR /src
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

FROM python:3.12.15-slim AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY --from=deps /opt/venv /opt/venv
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin shop \
    && install -d -o shop -g shop /var/lib/shop
# State that must survive a container restart (common/state.py, ADR-19).
ENV CS_STATE_DIR=/var/lib/shop
WORKDIR /app
COPY common/ common/

FROM runtime AS api
ARG CS_RELEASE
RUN test -n "$CS_RELEASE"
ENV CS_RELEASE=${CS_RELEASE}
COPY releases.yaml ./
COPY api/ api/
USER shop
EXPOSE 8000
CMD ["python", "-m", "api"]

FROM runtime AS worker
ARG CS_RELEASE
RUN test -n "$CS_RELEASE"
ENV CS_RELEASE=${CS_RELEASE}
COPY releases.yaml ./
COPY worker/ worker/
USER shop
EXPOSE 8001
CMD ["python", "-m", "worker"]

FROM runtime AS payments
ARG CS_RELEASE
RUN test -n "$CS_RELEASE"
ENV CS_RELEASE=${CS_RELEASE}
COPY payments/ payments/
USER shop
EXPOSE 8002
CMD ["python", "-m", "payments"]

FROM runtime AS loadgen
ARG CS_RELEASE
RUN test -n "$CS_RELEASE"
ENV CS_RELEASE=${CS_RELEASE}
COPY loadgen/ loadgen/
USER shop
EXPOSE 8003
CMD ["python", "-m", "loadgen"]
