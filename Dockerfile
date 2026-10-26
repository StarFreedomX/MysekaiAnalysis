FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY mysekai_web ./mysekai_web
COPY static ./static
COPY assets ./assets
RUN useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
USER app
ENV HOST=0.0.0.0 PORT=3939 DATABASE_PATH=/data/mysekai.sqlite3
EXPOSE 3939
CMD ["python", "-m", "mysekai_web"]
