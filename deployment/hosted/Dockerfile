FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
WORKDIR /app
RUN pip install --no-cache-dir uv==0.10.0
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --python /usr/local/bin/python
COPY examples/load ./examples/load
COPY examples/support ./examples/support
COPY deployment/hosted ./deployment/hosted
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app AGENTS_CLI_TELEMETRY=0
CMD ["uvicorn", "deployment.hosted.server:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
