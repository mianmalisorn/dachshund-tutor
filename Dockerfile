FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 APP_ENV=production TUTOR_DATA_DIR=/data DATABASE_URL=sqlite:////data/tutor.db UPLOAD_ROOT=/data/uploads SECURE_COOKIES=true TELEGRAM_ENABLED=false PERSONAL_TELEGRAM_ENABLED=false
WORKDIR /app
COPY deploy/requirements.lock.txt /app/deploy/requirements.lock.txt
RUN python -m pip install --no-cache-dir -r deploy/requirements.lock.txt
COPY . /app
RUN mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
CMD ["python", "-m", "scripts.amvera_runtime"]
