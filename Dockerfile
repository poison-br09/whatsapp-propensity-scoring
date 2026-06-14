FROM node:22-slim AS bridge-deps

WORKDIR /build/whatsapp_bridge

COPY whatsapp_bridge/package.json whatsapp_bridge/package-lock.json ./

RUN npm ci


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY pyproject.toml README.md ./
COPY app ./app
COPY whatsapp_bridge ./whatsapp_bridge

COPY --from=bridge-deps /usr/local/bin/node /usr/local/bin/node
COPY --from=bridge-deps /build/whatsapp_bridge/node_modules ./whatsapp_bridge/node_modules

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

RUN mkdir -p /app/whatsapp_bridge/baileys_auth_info /app/logs \
    && chown -R app:app /app/whatsapp_bridge/baileys_auth_info /app/logs

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
