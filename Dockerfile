# OPTIONAL learning exercise. The project runs perfectly with a plain venv;
# nothing here is required. Build only if you want to practise Docker.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
# Playwright's browser download is deliberately NOT run here: the image stays
# small and the fake source needs no browser. Add
#   RUN playwright install --with-deps chromium
# yourself if you enable the real adapter.
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/

# data/ is a volume so your SQLite file survives container rebuilds.
VOLUME ["/app/data"]
CMD ["python", "-m", "app.main", "run", "--no-bot"]
