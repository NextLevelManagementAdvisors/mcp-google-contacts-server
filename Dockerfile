FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml setup.py README.md LICENSE ./
COPY mcp_google_contacts_server ./mcp_google_contacts_server

RUN pip install --no-cache-dir .

RUN useradd --system --uid 1001 --home /app --shell /usr/sbin/nologin app \
    && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/ || exit 1

ENTRYPOINT ["mcp-google-contacts"]
CMD ["--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
