# Lab targets. Build context: deploy/lab/target.
FROM python:3.14.4-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 iptables \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pyyaml==6.0.3

COPY listen.py /lab/listen.py

# Root on purpose: the container sets addresses, routes and firewall rules in
# its own network namespace. It runs in the lab only.
CMD ["python", "-u", "/lab/listen.py"]
