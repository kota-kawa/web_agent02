#!/bin/bash
# set -e # Removed to allow the script to continue even if some commands fail

# スクリプトがあるディレクトリの絶対パスを取得
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
# プロジェクトルート（webarena_dataの親ディレクトリ）
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== WebArena 環境構築・起動スクリプト ==="
echo "作業ディレクトリ: $SCRIPT_DIR"

# イメージファイルの定義 (ファイル名:イメージ名)

declare -A IMAGES=(
    ["shopping_final_0712.tar"]="shopping_final_0712"
    # ["shopping_admin_final_0719.tar"]="shopping_admin_final_0719"
    # ["postmill-populated-exposed-withimg.tar"]="postmill-populated-exposed-withimg"
    # ["gitlab-populated-final-port8023.tar"]="gitlab-populated-final-port8023"
)

# 1. Dockerイメージのロード
echo ""
echo "--- 1. Dockerイメージの確認とロード ---"

for file in "${!IMAGES[@]}"; do
    image_name="${IMAGES[$file]}"
    file_path="$SCRIPT_DIR/$file"

    if [ -f "$file_path" ]; then
        # Check if image exists using 'docker images' for broader compatibility
        if docker images --format "{{.Repository}}:{{.Tag}}" | grep -q "^${image_name}"; then
             echo "✔ イメージ '$image_name' は既に存在します (docker images で確認)。ロードをスキップします。"
        elif docker image inspect "$image_name" > /dev/null 2>&1; then
             echo "✔ イメージ '$image_name' は既に存在します (inspect で確認)。ロードをスキップします。"
        else
             echo "➤ ファイル '$file' からイメージをロードしています..."
             echo "   ⚠️ 注意: ファイルサイズが大きいため、完了までに数分〜数十分かかる場合があります。"
             echo "   ⚠️ 別のターミナルで 'top' や 'docker system df' を実行して動作を確認できます。"
             if command -v pv > /dev/null 2>&1; then
                 echo "   (pv を使用して進捗を表示中...)"
                 if pv "$file_path" | docker load; then
                     echo "✔ ロード完了: $image_name"
                 else
                     echo "❌ エラー: ファイル '$file' からのイメージロードに失敗しました。"
                 fi
             else
                 echo "   (pv がインストールされていないため、進捗は表示されません。インストールするには 'sudo apt install pv' または同等のコマンドを実行してください)"
                 if docker load -i "$file_path"; then
                     echo "✔ ロード完了: $image_name"
                 else
                     echo "❌ エラー: ファイル '$file' からのイメージロードに失敗しました。"
                 fi
             fi
        fi
    else
        echo "⚠️ 警告: ファイル '$file' が見つかりません。このイメージはロードされません。"
    fi
done

# 2. ネットワークの作成 (存在しない場合)
echo ""
echo "--- 2. ネットワークの確認 ---"
NETWORK_NAME="multi_agent_platform_net"
if docker network ls | grep -q "$NETWORK_NAME"; then
    echo "✔ ネットワーク '$NETWORK_NAME' は既に存在します。"
else
    echo "➤ ネットワーク '$NETWORK_NAME' を作成しています..."
    docker network create "$NETWORK_NAME"
    echo "✔ 作成完了"
fi

# 3. Docker Compose で起動
echo ""
echo "--- 3. WebArena サービスの起動 ---"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.webarena.yml"

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "❌ エラー: Docker Composeファイルが見つかりません: $COMPOSE_FILE"
    exit 1
fi

echo "➤ プロジェクトルートから Docker Compose を実行します..."
# プロジェクトルートに移動して実行（パス解決のため）
cd "$PROJECT_ROOT"

# .envファイルがルートに必要かもしれないので確認（オプション）
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    echo "ℹ️ .env が見つからないため、.env.example をコピーします..."
    cp .env.example .env
fi

# 実行
# -f でファイルを指定し、-p でプロジェクト名を指定
docker compose -f "$COMPOSE_FILE" up -d

echo ""
echo "➤ Shoppingサービスの初期設定を行っています (30秒待機)..."
sleep 30

echo "   - Base URLの設定..."
docker exec shopping /var/www/magento2/bin/magento setup:store-config:set --base-url='http://shopping/'

echo "   - データベースの更新..."
docker exec shopping mysql -u magentouser -pMyPassword magentodb -e 'UPDATE core_config_data SET value="http://shopping/" WHERE path = "web/secure/base_url";'

echo "   - キャッシュのクリア..."
docker exec shopping /var/www/magento2/bin/magento cache:flush

echo ""
echo "=== 完了しました ==="
echo "各サービスへのアクセス:"
echo " - Shopping: http://localhost:7770"
# echo " - Shopping Admin: http://localhost:7780"
# echo " - Forum (Reddit clone): http://localhost:9999"
# echo " - GitLab: http://localhost:8023"
