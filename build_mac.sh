#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  NEXUS PRO — macOS .app ビルドスクリプト
#  実行: chmod +x build_mac.sh && ./build_mac.sh
# ═══════════════════════════════════════════════════════════════

set -euo pipefail
IFS=$'\n\t'

cd "$(cd "$(dirname "$0")" && pwd)"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

APP_NAME="NEXUS PRO"
MODEL_NAME="qwen2.5-coder:7b"
BUNDLED_MODELS_DIR="bundled_models"
MODEL_DIR="$BUNDLED_MODELS_DIR/$MODEL_NAME"
MODELFILE_PATH="$MODEL_DIR/Modelfile"
OLLAMA_SETUP_ASSETS_DIR="ollama_setup_assets"
APP_PATH="dist/${APP_NAME}.app"

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║    NEXUS PRO — macOS .app ビルド         ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo -e "${RED}❌ Python3 が見つかりません${NC}"
  exit 1
fi
PYTHON="$(command -v python3)"

echo -e "${GREEN}✓ Python: $($PYTHON --version)${NC}"

mkdir -p "$BUNDLED_MODELS_DIR"
if [[ ! -d "$MODEL_DIR" ]]; then
  echo -e "${RED}❌ 同梱モデルフォルダが見つかりません: $MODEL_DIR${NC}"
  echo "   例: mkdir -p '$MODEL_DIR'"
  echo "       その中に Modelfile と必要ファイルを配置してください。"
  exit 1
fi
if [[ ! -f "$MODELFILE_PATH" ]]; then
  echo -e "${RED}❌ Modelfile が見つかりません: $MODELFILE_PATH${NC}"
  exit 1
fi

echo -e "${GREEN}✓ 同梱モデル確認OK${NC}"

echo -e "${CYAN}[1/5] 依存ライブラリを準備中...${NC}"
"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --quiet flask requests pywebview pyinstaller Pillow
"$PYTHON" -m pip install --quiet duckduckgo-search trafilatura beautifulsoup4 lxml 2>/dev/null || true
"$PYTHON" -m pip install --quiet chromadb sentence-transformers 2>/dev/null || true

echo -e "${CYAN}[2/5] アイコン生成...${NC}"
ICON_ARGS=()
if "$PYTHON" generate_icon.py; then
  if [[ -f "icon.icns" ]]; then
    ICON_ARGS=(--icon "icon.icns")
    echo -e "${GREEN}✓ アイコン生成完了${NC}"
  else
    echo -e "${YELLOW}⚠ icon.icns 未生成。アイコンなしで続行します${NC}"
  fi
else
  echo -e "${YELLOW}⚠ アイコン生成失敗。アイコンなしで続行します${NC}"
fi

echo -e "${CYAN}[3/5] 前回ビルドをクリア...${NC}"
rm -rf build/ dist/

echo -e "${CYAN}[4/5] .app をビルド中...${NC}"

EXTRA_DATA_ARGS=(--add-data ".:." --add-data "$BUNDLED_MODELS_DIR:bundled_models")
if [[ -d "$OLLAMA_SETUP_ASSETS_DIR" ]]; then
  EXTRA_DATA_ARGS+=(--add-data "$OLLAMA_SETUP_ASSETS_DIR:ollama_setup_assets")
  echo -e "${GREEN}✓ Ollama セットアップ素材を同梱します: $OLLAMA_SETUP_ASSETS_DIR${NC}"
else
  echo -e "${YELLOW}⚠ Ollama セットアップ素材フォルダなし（任意）: $OLLAMA_SETUP_ASSETS_DIR${NC}"
fi

"$PYTHON" -m PyInstaller \
  --clean \
  --noconfirm \
  --windowed \
  --name "$APP_NAME" \
  "${ICON_ARGS[@]}" \
  --osx-bundle-identifier com.nexuspro.app \
  --hidden-import flask \
  --hidden-import werkzeug \
  --hidden-import werkzeug.serving \
  --hidden-import jinja2 \
  --hidden-import click \
  --hidden-import sqlite3 \
  --hidden-import requests \
  --hidden-import urllib3 \
  --hidden-import certifi \
  --hidden-import webview \
  --hidden-import webview.platforms.cocoa \
  --hidden-import chromadb \
  --hidden-import sentence_transformers \
  --hidden-import duckduckgo_search \
  --hidden-import trafilatura \
  --hidden-import bs4 \
  --hidden-import lxml \
  "${EXTRA_DATA_ARGS[@]}" \
  nexus_pro_mac.py

echo -e "${CYAN}[5/5] 生成物確認...${NC}"
if [[ -d "$APP_PATH" ]]; then
  ABS_APP_PATH="$(cd "$(dirname "$APP_PATH")" && pwd)/$(basename "$APP_PATH")"
  echo -e "${GREEN}✓ 生成成功: $ABS_APP_PATH${NC}"
  echo -e "${GREEN}✓ ログ保存先: ~/Library/Logs/NEXUS_PRO/startup.log${NC}"
else
  echo -e "${RED}❌ .app が見つかりません: $APP_PATH${NC}"
  exit 1
fi
