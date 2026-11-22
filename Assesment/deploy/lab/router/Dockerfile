# Lab router. Build context: deploy/lab/router.
FROM alpine:3.24.4

RUN apk add --no-cache iproute2 iptables

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
