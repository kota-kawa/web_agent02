# Upstream noVNC Details

- `Dockerfile.chrome` builds on the `selenium/standalone-chrome:latest` image, so the Selenium release dictates the bundled noVNC version.
- The SeleniumHQ/docker-selenium 4.37.0-20251020 release that backs `latest` sets `ARG NOVNC_VERSION="v1.6.0"` in `NodeBase/Dockerfile`, meaning the image ships with noVNC 1.6.0 before patching and installs it under `/opt/bin/noVNC`.
- The custom build step in `Dockerfile.chrome` now replaces `/opt/bin/noVNC/app/ui.js` and `/opt/bin/noVNC/core/util/browser.js` with the upstream commit `8edb3d282eb9ebb138b0f9a4baacb90bb4c4427e`, adding the VideoFrame `close()` fix and tagging the local version as `1.6.0+frame-close-patch`.
