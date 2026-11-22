# Spike scanner image. Build context: the Assesment/ directory.
#
# The container set-up of architecture section 8.4:
#   - the process runs as a non-root user;
#   - a non-root process gets no container capability by itself, so the image
#     puts the cap_net_raw file capability on its OWN copy of the interpreter.
#     The ordinary "python" in the image stays without any capability;
#   - Compose drops all capabilities and adds NET_RAW only;
#   - "no-new-privileges" must NOT be set for this container: it switches file
#     capabilities off.
FROM python:3.14.4-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcap2-bin \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pyyaml==6.0.3 \
    # The copy sits beside the original, so it finds the same standard library.
    && cp "$(readlink -f /usr/local/bin/python3)" /usr/local/bin/python-netraw \
    && setcap cap_net_raw+ep /usr/local/bin/python-netraw \
    && useradd --uid 10003 --no-create-home --shell /usr/sbin/nologin spike

# The kept code is imported from the package, not copied into the spike:
# packet builder, reply matcher and port lists. None of it needs a dependency.
COPY backend/src/ndm/__init__.py /app/ndm/__init__.py
COPY backend/src/ndm/core/__init__.py backend/src/ndm/core/ports.py /app/ndm/core/
COPY backend/src/ndm/core/data /app/ndm/core/data
COPY backend/src/ndm/scanner /app/ndm/scanner
COPY spike/scanner/spike_scan.py /app/spike_scan.py

ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1
USER 10003

ENTRYPOINT ["python-netraw", "/app/spike_scan.py"]
