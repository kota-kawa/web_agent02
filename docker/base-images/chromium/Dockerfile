ARG BASE_TAG=latest
FROM browseruse/base-system:${BASE_TAG}

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends procps wmctrl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /tmp
COPY pyproject.toml ./

# Install chromium browser using temporary playwright installation
RUN --mount=type=cache,target=/root/.cache,sharing=locked \
    echo "Installing chromium browser via temporary playwright..." && \
    pip install --no-cache-dir playwright && \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright playwright install chromium --with-deps --no-shell && \
    ln -s /opt/playwright/chromium-*/chrome-linux/chrome /usr/bin/chromium-browser && \
    chmod -R 755 /opt/playwright && \
    pip uninstall playwright -y && \
    rm -f pyproject.toml

RUN mkdir -p /opt/browseruse/bin
COPY docker/base-images/chromium/maximize_chrome.sh /opt/browseruse/bin/maximize_chrome.sh
COPY docker/base-images/chromium/entrypoint.sh /opt/browseruse/bin/entrypoint.sh
RUN chmod +x /opt/browseruse/bin/maximize_chrome.sh /opt/browseruse/bin/entrypoint.sh
