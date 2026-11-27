FROM python:3.12-slim
WORKDIR /app
COPY harness/deploy/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY harness/__init__.py /app/harness/
COPY harness/core/__init__.py harness/core/common.py harness/core/evidence.py harness/core/evidence_mcp.py /app/harness/core/
COPY logging/archive/harness-evidence.json /app/evidence.json
ENV TIFORGE_EVIDENCE_FILE=/app/evidence.json TIFORGE_MCP_HOST=0.0.0.0 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8000
CMD ["python", "-m", "harness.core.evidence_mcp"]
