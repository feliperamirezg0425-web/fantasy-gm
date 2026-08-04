FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Seed mock data on first boot if the DB doesn't exist yet, then start the API.
# In production (USE_MOCK_DATA=false with a real DATABASE_URL) skip seeding and
# run migrations instead.
CMD ["sh", "-c", "python -m app.seed_mock_data || true; uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
