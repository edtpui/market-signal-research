# Runtime for Market Signal Research: the interpreter and its dependencies only. The project is not copied in:
# compose.yaml mounts it read-only at /workspace/strategy_lab (and the research data at /workspace/data), so the
# paths in config/settings.toml resolve as on the host and run provenance (git commit, source hashes) stays real.
# Versions are the ones verified in ../.venv-research (see pyproject.toml).
FROM python:3.14-slim-bookworm

# git: catalog.git_state() records the commit and whether src/ is dirty with every run.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory /workspace/strategy_lab

RUN pip install --no-cache-dir \
    numpy==2.5.4 pandas==3.1.0 duckdb==2.0.0 pydantic==2.14.1 fastapi==0.162.0 uvicorn==0.54.0 requests==2.34.2 \
    pytest==9.1.1

ENV PYTHONPATH=/workspace/strategy_lab/src \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Not root: finished experiment directories are protected by read-only file modes, which root would ignore.
# compose.yaml's state volume must be owned by this uid (it was chowned when it was seeded).
RUN useradd --uid 1000 --create-home msr
USER msr

WORKDIR /workspace/strategy_lab
EXPOSE 8070

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8070/api/v1/health', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "strategy_lab", "serve", "--host", "0.0.0.0", "--port", "8070"]
