FROM python:3.13-slim

WORKDIR /app
COPY tests/fakes /app/tests/fakes
COPY tests/__init__.py /app/tests/__init__.py

RUN pip install --no-cache-dir uv && uv pip install --system --no-cache "uvicorn>=0.30"

EXPOSE 8080
HEALTHCHECK --interval=2s --timeout=2s --retries=15 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz').status==200 else 1)"

CMD ["uvicorn", "tests.fakes.app:app", "--host", "0.0.0.0", "--port", "8080"]
