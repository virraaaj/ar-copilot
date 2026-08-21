# --- Stage 1: build the UI -------------------------------------------------
FROM node:20-slim AS ui-build
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

# --- Stage 2: backend + serve the built UI ----------------------------------
FROM python:3.12-slim
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY --from=ui-build /ui/dist/ ./ui/dist/

ENV PYTHONUNBUFFERED=1
EXPOSE 8090

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
