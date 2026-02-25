FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# gosu lets the entrypoint drop from root to the app user after
# fixing bind-mount ownership.
RUN apt-get update && apt-get install -y --no-install-recommends gosu git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/ai-agent

COPY ai-agent/requirements.txt /app/ai-agent/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install -r requirements.txt

COPY ai-agent /app/ai-agent

RUN groupadd -r app && useradd -r -g app app && \
    chown -R app:app /app/ai-agent && \
    chmod +x /app/ai-agent/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/ai-agent/entrypoint.sh"]
CMD ["uvicorn", "service.api:app", "--host", "0.0.0.0", "--port", "8000"]
