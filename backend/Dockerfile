# syntax=docker/dockerfile:1
# The backend image (architecture §2.2, §2.3, ADR-09): one image, two processes
# (backend-api, backend-worker), plus one-off tools such as the seed command.
# The contracts package comes from the `contracts` build context:
#   docker build --build-context contracts=contracts -t oncallpilot/backend:0.1.0 backend
#
# Runs as an unprivileged user with no Docker socket. Secrets arrive only as
# environment variables at run time; none is baked into the image.

FROM python:3.12.15-slim AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir uv==0.12.7
# Same layout as the repository, so the lockfile's ../contracts/python path holds.
COPY --from=contracts python /src/contracts/python
WORKDIR /src/backend
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project --no-editable
COPY oncallpilot/ oncallpilot/
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12.15-slim
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY --from=build /opt/venv /opt/venv
RUN useradd --system --uid 10002 --no-create-home --shell /usr/sbin/nologin backend
WORKDIR /app
# The detector rules; their hash is detector_config_version (relative to /app).
COPY config/ config/
USER backend
EXPOSE 8000
CMD ["uvicorn", "--factory", "oncallpilot.api.app:create_production_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
