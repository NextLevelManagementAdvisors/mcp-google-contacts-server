FROM python:3.12-slim
WORKDIR /opt/contacts-mcp
COPY pyproject.toml requirements.txt setup.py README.md ./
COPY mcp_google_contacts_server ./mcp_google_contacts_server
RUN pip install --no-cache-dir .
EXPOSE 8020
CMD ["mcp-google-contacts", "--transport", "http", "--host", "127.0.0.1", "--port", "8020", "--credentials-file", "/opt/contacts-mcp/credentials.json"]
