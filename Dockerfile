FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
WORKDIR /app
COPY backend/requirements.lock /app/backend/requirements.lock
RUN pip install --no-cache-dir -r backend/requirements.lock \
    && playwright install --with-deps chromium \
    && chmod -R a+rX /opt/browsers
COPY engine /app/engine
COPY backend /app/backend
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser
WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
