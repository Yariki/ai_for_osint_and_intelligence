# scanner-netinit: a one-shot container that installs two firewall rules on the
# host and exits. Build context: deploy/scanner-netinit.
#
# Run it on the host network with NET_ADMIN and nothing else:
#
#   docker run --rm --network host --cap-drop ALL --cap-add NET_ADMIN ndm-scanner-netinit:local
FROM alpine:3.24.4

# The release this image belongs to. Not a secret: the version and the commit.
ARG NDM_VERSION=dev
ARG NDM_REVISION=unknown
LABEL org.opencontainers.image.title="ndm-scanner-netinit" \
      org.opencontainers.image.version="${NDM_VERSION}" \
      org.opencontainers.image.revision="${NDM_REVISION}" \
      org.opencontainers.image.description="Network Discovery Map"

# "iptables" is the nftables back end; "iptables-legacy" is there for hosts that use it.
RUN apk add --no-cache iptables iptables-legacy

COPY install-rules.sh /usr/local/bin/install-rules.sh
RUN chmod +x /usr/local/bin/install-rules.sh

ENTRYPOINT ["/usr/local/bin/install-rules.sh"]
CMD ["install"]
