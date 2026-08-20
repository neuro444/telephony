FROM python:3.11-slim

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/audio /app/orders && chown -R app:app /app

USER app

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
