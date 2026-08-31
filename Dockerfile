FROM python:3.12-slim AS builder

WORKDIR /build

RUN pip install --no-cache-dir "uv>=0.5"

COPY pyproject.toml .
# Resolve + pin all transitive deps, then install to system Python
RUN uv pip compile pyproject.toml -o requirements.txt && \
    uv pip install --system --no-cache -r requirements.txt


FROM python:3.12-slim

WORKDIR /app

# Copy only the installed packages and the uvicorn binary from the builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn

COPY app/ app/

EXPOSE 8000

# Non-root user — no name needed, just a UID/GID
USER 1000:1000

# PORT env var lets callers override the bound port without rebuilding
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
