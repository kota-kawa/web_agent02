#!/bin/bash
set -e

# WebArena Setup Script for Local Deployment
# This script downloads necessary Docker images and datasets for running WebArena locally.
# Requires: curl, docker

DEST_DIR="${WEBARENA_DIR:-./webarena_data}"
mkdir -p "$DEST_DIR"

echo "Downloading WebArena environment images..."

# 1. Shopping (Magento)
if [ ! -f "$DEST_DIR/shopping_final_0712.tar" ]; then
    echo "Downloading Shopping image..."
    curl -L -o "$DEST_DIR/shopping_final_0712.tar" "http://metis.lti.cs.cmu.edu/webarena-images/shopping_final_0712.tar"
    echo "Loading Shopping image..."
    docker load --input "$DEST_DIR/shopping_final_0712.tar"
fi

# 2. Shopping Admin
if [ ! -f "$DEST_DIR/shopping_admin_final_0719.tar" ]; then
    echo "Downloading Shopping Admin image..."
    curl -L -o "$DEST_DIR/shopping_admin_final_0719.tar" "http://metis.lti.cs.cmu.edu/webarena-images/shopping_admin_final_0719.tar"
    echo "Loading Shopping Admin image..."
    docker load --input "$DEST_DIR/shopping_admin_final_0719.tar"
fi

# 3. Reddit (Postmill)
if [ ! -f "$DEST_DIR/postmill-populated-exposed-withimg.tar" ]; then
    echo "Downloading Forum (Reddit) image..."
    curl -L -o "$DEST_DIR/postmill-populated-exposed-withimg.tar" "http://metis.lti.cs.cmu.edu/webarena-images/postmill-populated-exposed-withimg.tar"
    echo "Loading Forum image..."
    docker load --input "$DEST_DIR/postmill-populated-exposed-withimg.tar"
fi

# 4. GitLab
if [ ! -f "$DEST_DIR/gitlab-populated-final-port8023.tar" ]; then
    echo "Downloading GitLab image..."
    curl -L -o "$DEST_DIR/gitlab-populated-final-port8023.tar" "http://metis.lti.cs.cmu.edu/webarena-images/gitlab-populated-final-port8023.tar"
    echo "Loading GitLab image..."
    docker load --input "$DEST_DIR/gitlab-populated-final-port8023.tar"
fi

# 5. Wikipedia (ZIM file)
if [ ! -f "$DEST_DIR/wikipedia_en_all_maxi_2022-05.zim" ]; then
    echo "Downloading Wikipedia ZIM file (This is large ~90GB)..."
    # Note: Using a dummy command or asking user confirmation might be better for 90GB
    # But sticking to instructions:
    curl -L -o "$DEST_DIR/wikipedia_en_all_maxi_2022-05.zim" "http://metis.lti.cs.cmu.edu/webarena-images/wikipedia_en_all_maxi_2022-05.zim"
fi

echo "Images loaded. Ready to run docker compose."
