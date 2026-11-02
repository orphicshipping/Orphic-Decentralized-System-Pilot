FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
WORKDIR /srv/ncp
COPY wheel/ /tmp/wheel/
COPY tools/mcp-operator-console/requirements.txt /tmp/requirements.txt
RUN --mount=type=secret,id=proxy_ca,required=true PIP_CERT=/run/secrets/proxy_ca \
    python -m pip install --no-cache-dir /tmp/wheel/*.whl -r /tmp/requirements.txt
COPY tools/mcp-operator-console/console.py tools/mcp-operator-console/console.py
COPY examples/numerical_operations/prepare.py examples/numerical_operations/prepare.py
COPY tools/private-mcp-host/adapter.py tools/private-mcp-host/adapter.py
RUN chmod -R a+rX tools examples && useradd --uid 10001 --create-home ncp && mkdir runtime && chown ncp:ncp runtime
USER 10001
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "adapter:app", "--app-dir", "/srv/ncp/tools/private-mcp-host", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

