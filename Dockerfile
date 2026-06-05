FROM python:3.12-slim

RUN adduser --disabled-password --no-create-home --gecos "" mcp

WORKDIR /app

# Install the package from PyPI
RUN pip install --no-cache-dir jaeger-mcp

USER mcp

ENTRYPOINT ["jaeger-mcp"]
