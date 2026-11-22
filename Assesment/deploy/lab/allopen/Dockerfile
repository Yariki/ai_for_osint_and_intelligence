# Lab target for check 5.5: a /24 in which every address answers on every TCP
# port (question Q1). Build context: deploy/lab/allopen.
FROM python:3.14.4-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 iptables \
    && rm -rf /var/lib/apt/lists/*

COPY listen.py /lab/listen.py

# Root on purpose: the container sets a route and a NAT rule in its own
# network namespace. It runs in the lab only.
CMD ["python", "-u", "/lab/listen.py"]
