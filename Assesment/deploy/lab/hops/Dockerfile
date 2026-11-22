# A router of the lab's router chain (traceroute checks of Phase 3).
# Build context: deploy/lab/hops.
FROM alpine:3.24.4

RUN apk add --no-cache iproute2 iptables

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
