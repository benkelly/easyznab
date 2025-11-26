FROM python:3.12-slim

WORKDIR /app

RUN pip install fastapi uvicorn httpx

COPY main.py /app/main.py

ENV PROXY_API_KEY=changeme
ENV EASYNEWS_USER=""
ENV EASYNEWS_PASS=""

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

