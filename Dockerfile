FROM python:3.12-slim

WORKDIR /app

# Install the package from PyPI
RUN pip install --no-cache-dir jaeger-mcp

ENTRYPOINT ["jaeger-mcp"]
