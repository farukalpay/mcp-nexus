FROM python:3.12-slim

LABEL maintainer="Lightcap AI <dev@lightcap.ai>"
LABEL description="MCP Nexus — Remote server management via Model Context Protocol"

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY mcp_nexus/ mcp_nexus/
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Non-root user
RUN useradd -m nexus
USER nexus

EXPOSE 8766

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -m mcp_nexus health || exit 1

ENTRYPOINT ["python", "-m", "mcp_nexus"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8766"]
