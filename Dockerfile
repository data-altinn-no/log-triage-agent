FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY agents ./agents
COPY api ./api
COPY shared ./shared

EXPOSE 8081

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8081"]
