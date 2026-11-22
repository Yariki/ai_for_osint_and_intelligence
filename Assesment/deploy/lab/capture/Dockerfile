# tcpdump for the lab suite's packet captures, on a lab host without sudo.
# backend/tests/lab/conftest.py builds and runs it on the host network with
# NET_RAW and NET_ADMIN only. It is not part of the product.
FROM alpine:3.22
RUN apk add --no-cache tcpdump
ENTRYPOINT ["tcpdump"]
