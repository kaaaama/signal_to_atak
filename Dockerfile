FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

COPY . .

RUN pip install --no-cache-dir -r requirements.txt
RUN chown -R appuser:appgroup /app

USER appuser

CMD ["python", "-m", "app.main"]