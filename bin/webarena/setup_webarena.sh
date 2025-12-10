#!/usr/bin/env bash
set -euo pipefail

# WebArena Setup Script (Robust Mirrors + Better Validation)
# - Prefer CMU mirror, fallback to Archive.org
# - Avoid HTML-biased Accept header
# - Fail fast on HTTP errors
# - Detect HTML error bodies
# - Keep behavior: download tar -> docker load

DEST_DIR="${WEBARENA_DIR:-./webarena_data}"
mkdir -p "$DEST_DIR"

# Browser-like User-Agent (still useful for some CDNs)
USER_AGENT="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# Minimum size (MB) to consider a file "not obviously broken"
# These images are typically large; keep conservative thresholds to avoid false deletes.
MIN_MB_SHOPPING=100
MIN_MB_SHOPPING_ADMIN=100
MIN_MB_FORUM=100
MIN_MB_GITLAB=100
MIN_MB_WIKI=100

get_size_mb() {
    local f="$1"
    if [ ! -f "$f" ]; then
        echo 0
        return 0
    fi
    local bytes
    bytes=$(stat -c%s "$f" 2>/dev/null || echo 0)
    # Convert to MB (integer)
    echo $(( bytes / 1024 / 1024 ))
}

looks_like_html() {
    local f="$1"
    # 1) "file" command check (if available)
    if command -v file >/dev/null 2>&1; then
        if file "$f" | grep -qi "HTML"; then
            return 0
        fi
    fi
    # 2) head sniff
    if head -c 512 "$f" 2>/dev/null | tr -d '\000' | grep -qi "<html\|<!doctype html"; then
        return 0
    fi
    return 1
}

curl_download() {
    local url="$1"
    local output="$2"

    # Use --fail to treat 4xx/5xx as errors
    # Use -L for redirects
    # Use retries for flaky networks
    curl -L --fail \
        --retry 5 --retry-delay 2 --retry-connrefused \
        -A "$USER_AGENT" \
        -H "Referer: https://archive.org/" \
        -H "Accept: */*" \
        -o "$output" \
        "$url"
}

download_file() {
    local output="$1"
    local min_mb="$2"
    shift 2
    local urls=("$@")

    echo "Processing $output..."

    # 1. Check existing file size
    if [ -f "$output" ]; then
        local size
        size=$(get_size_mb "$output")
        if [ "$size" -lt "$min_mb" ]; then
            echo "  - Existing file seems incomplete or corrupt (${size}MB < ${min_mb}MB). Deleting."
            rm -f "$output"
        else
            echo "  - File exists and looks large enough (${size}MB). Skipping download."
            return 0
        fi
    fi

    # 2. Try mirrors in order
    local success=0
    for url in "${urls[@]}"; do
        echo "  - Trying: $url"

        # Because we use set -e, wrap curl in if to catch failure without exiting function
        if curl_download "$url" "$output"; then
            # Basic validation
            local size
            size=$(get_size_mb "$output")
            if [ "$size" -lt "$min_mb" ]; then
                echo "    * Downloaded but too small (${size}MB < ${min_mb}MB). Treating as failure."
                rm -f "$output"
                continue
            fi
            if looks_like_html "$output"; then
                echo "    * Downloaded file looks like HTML error page. Treating as failure."
                rm -f "$output"
                continue
            fi

            echo "    * Download OK (${size}MB)."
            success=1
            break
        else
            echo "    * Download failed for this mirror."
            rm -f "$output" 2>/dev/null || true
        fi
    done

    if [ "$success" -ne 1 ]; then
        echo "Error: All mirrors failed for $output."
        echo "Tips:"
        echo "  - Try again later (Archive.org rate limits are common)."
        echo "  - Ensure outbound HTTP/HTTPS is allowed on your network."
        exit 1
    fi
}

echo "Starting WebArena environment setup..."

# Mirrors as documented by WebArena-related repos/guides:
# - Google Drive
# - Archive.org
# - CMU server (metis.lti.cs.cmu.edu)
# We prioritize CMU -> Archive here for simplicity and robustness.:contentReference[oaicite:1]{index=1}

# --- 1. Shopping (Magento) ---
download_file \
    "$DEST_DIR/shopping_final_0712.tar" \
    "$MIN_MB_SHOPPING" \
    "http://metis.lti.cs.cmu.edu/webarena-images/shopping_final_0712.tar" \
    "https://archive.org/download/webarena-env-shopping-image/shopping_final_0712.tar"

echo "Loading Shopping image..."
docker load --input "$DEST_DIR/shopping_final_0712.tar"

# --- 2. Shopping Admin ---
download_file \
    "$DEST_DIR/shopping_admin_final_0719.tar" \
    "$MIN_MB_SHOPPING_ADMIN" \
    "http://metis.lti.cs.cmu.edu/webarena-images/shopping_admin_final_0719.tar" \
    "https://archive.org/download/webarena-env-shopping-admin-image/shopping_admin_final_0719.tar"

echo "Loading Shopping Admin image..."
docker load --input "$DEST_DIR/shopping_admin_final_0719.tar"

# --- 3. Reddit (Postmill) ---
download_file \
    "$DEST_DIR/postmill-populated-exposed-withimg.tar" \
    "$MIN_MB_FORUM" \
    "http://metis.lti.cs.cmu.edu/webarena-images/postmill-populated-exposed-withimg.tar" \
    "https://archive.org/download/webarena-env-forum-image/postmill-populated-exposed-withimg.tar"

echo "Loading Forum image..."
docker load --input "$DEST_DIR/postmill-populated-exposed-withimg.tar"

# --- 4. GitLab ---
download_file \
    "$DEST_DIR/gitlab-populated-final-port8023.tar" \
    "$MIN_MB_GITLAB" \
    "http://metis.lti.cs.cmu.edu/webarena-images/gitlab-populated-final-port8023.tar" \
    "https://archive.org/download/webarena-env-gitlab-image/gitlab-populated-final-port8023.tar"

echo "Loading GitLab image..."
docker load --input "$DEST_DIR/gitlab-populated-final-port8023.tar"

# --- 5. Wikipedia (ZIM file) ---
# If user wants to skip this huge download
if [ -z "${SKIP_WIKI:-}" ]; then
    echo "Downloading Wikipedia ZIM file (very large)..."
    download_file \
        "$DEST_DIR/wikipedia_en_all_maxi_2022-05.zim" \
        "$MIN_MB_WIKI" \
        "https://archive.org/download/wikipedia_en_all_maxi_2022-05/wikipedia_en_all_maxi_2022-05.zim"
else
    echo "Skipping Wikipedia download (SKIP_WIKI is set)."
fi

echo "All images loaded successfully."
