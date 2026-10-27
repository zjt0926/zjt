FROM python:3.13-slim

WORKDIR /mcp

# curl for the compose healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./

ENV MCP_SERVER_NAME=campus-tools \
    MCP_SERVER_VERSION=1.0.0 \
    PORT=8090

EXPOSE 8090

# NOTE: MCP_AUTH_TOKEN is intentionally NOT baked into the image. It is
# supplied at runtime via the compose environment / .env so the secret
# never appears in the image layer or the repository.
CMD ["python", "app.py"]
