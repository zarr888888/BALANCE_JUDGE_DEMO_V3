FROM node:24-bookworm-slim AS frontend
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements-hosting.txt /app/backend/requirements-hosting.txt
RUN pip install --no-cache-dir -r /app/backend/requirements-hosting.txt
COPY backend/ /app/backend/
COPY cloud_start.py /app/cloud_start.py
COPY --from=frontend /web/dist /app/frontend/dist
RUN PYTHONPATH=/app/backend python -c "from balance_handoff.prepare import prepare;prepare('/opt/balance-model-template')"
ENV HOST=0.0.0.0 PORT=10000 BALANCE_STATE_DIR=/var/data/balance BALANCE_MODEL_TEMPLATE=/opt/balance-model-template
EXPOSE 10000
CMD ["python","cloud_start.py"]
