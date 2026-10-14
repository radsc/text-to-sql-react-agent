# Web UI on Cloud Run. Build context is ui/:
#   gcloud run deploy text-to-sql-ui --source ui ...
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app.py .

# Cloud Run sends traffic to $PORT (8080). AGENT_ENGINE_RESOURCE is set on the service,
# and the SDK authenticates as the service's own service account.
CMD streamlit run app.py \
    --server.port=${PORT:-8080} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
