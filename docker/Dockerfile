FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[all]"

COPY ragstack/ ragstack/

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "ragstack.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
