FROM selenium/standalone-chrome:latest

USER root

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends procps wmctrl && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/browseruse/bin
COPY docker/base-images/chromium/maximize_chrome.sh /opt/browseruse/bin/maximize_chrome.sh
COPY docker/base-images/chromium/entrypoint.sh /opt/browseruse/bin/entrypoint.sh
RUN chmod +x /opt/browseruse/bin/maximize_chrome.sh /opt/browseruse/bin/entrypoint.sh

USER seluser

ENTRYPOINT ["/opt/browseruse/bin/entrypoint.sh"]
CMD ["/opt/bin/entry_point.sh"]
