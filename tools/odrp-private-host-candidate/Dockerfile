ARG BASE_IMAGE=python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
FROM ${BASE_IMAGE}
USER root
ARG REUSE_BASE_DEPENDENCIES=0
WORKDIR /srv/ncp
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_INDEX=1
COPY wheelhouse/ /tmp/wheelhouse/
COPY requirements* /tmp/
RUN if [ "$REUSE_BASE_DEPENDENCIES" = 0 ]; then \
      test -s /tmp/requirements.hashed.lock && \
      python -m pip install --no-index --no-deps --no-compile --require-hashes --find-links=/tmp/wheelhouse -r /tmp/requirements.hashed.lock; \
    elif [ "$REUSE_BASE_DEPENDENCIES" != 1 ]; then exit 1; fi
COPY wheel/ wheel/
RUN python -m pip install --no-index --no-deps --no-compile --force-reinstall wheel/*.whl
# Remove only the prior image's owned service directories before installing the
# complete release closure; stale launch scripts must not survive an upgrade.
RUN rm -rf tools/private-mcp-host tools/mcp-operator-console examples/numerical_operations
COPY tools/ tools/
COPY examples/ examples/
RUN python -B -c "from ciw.private_release import verify_release; verify_release('/srv/ncp/wheel/release-manifest.json')"
RUN if ! id -u ncp >/dev/null 2>&1; then useradd --uid 10001 --create-home ncp; fi && \
    mkdir -p runtime && chown ncp:ncp runtime && chmod -R a+rX tools examples wheel
USER 10001
ENV ODSP_RELEASE_MANIFEST=/srv/ncp/wheel/release-manifest.json
EXPOSE 8080
CMD ["python", "-B", "-m", "uvicorn", "adapter:app", "--app-dir", "/srv/ncp/tools/private-mcp-host", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
