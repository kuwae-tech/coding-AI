"""
╔═══════════════════════════════════════════════════════════════╗
║           NEXUS PRO — macOS アプリ                            ║
╠═══════════════════════════════════════════════════════════════╣
║  直接実行:  python nexus_pro_mac.py                           ║
║  .app化:    ./build_mac.sh                                    ║
╚═══════════════════════════════════════════════════════════════╝
"""

import os
import sys
import io
import re
import ast
import json
import uuid
import time
import socket
import shutil
import sqlite3
import logging
import threading
import traceback
import subprocess
import webbrowser
from pathlib import Path
from datetime import datetime
from contextlib import redirect_stdout, redirect_stderr

import requests
from flask import Flask, Response, jsonify, request, stream_with_context

APP_SUPPORT = os.path.expanduser("~/Library/Application Support/NexusPro")
os.makedirs(APP_SUPPORT, exist_ok=True)

LOG_DIR = Path(os.path.expanduser("~/Library/Logs/NEXUS_PRO"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
STARTUP_LOG_PATH = LOG_DIR / "startup.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(STARTUP_LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("nexus_pro")

APP_PORT = 7100
OLLAMA = "http://localhost:11434"
MODEL_NAME = "qwen2.5-coder:7b"
OLLAMA_SETUP_FLAG = Path(APP_SUPPORT) / "ollama_setup_attempted.flag"
MODEL_READY_FLAG = Path(APP_SUPPORT) / f"model_ready_{MODEL_NAME.replace(':', '_')}.flag"

flask_app = Flask(__name__)
DB_PATH = Path(APP_SUPPORT) / "nexus_pro.db"
_tlocal = threading.local()


def _resource_base() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent.parent / "Resources"))
    return Path(__file__).resolve().parent


def bundled_model_dir() -> Path:
    return _resource_base() / "bundled_models" / MODEL_NAME


def _wait_server(port: int, timeout: int = 25) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), 1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _run_logged(cmd: list[str], cwd: str | None = None, timeout: int = 1800):
    logger.info("実行: %s", " ".join(cmd))
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    logger.info("終了コード: %s", p.returncode)
    if p.stdout:
        logger.info("stdout:\n%s", p.stdout[-5000:])
    if p.stderr:
        logger.info("stderr:\n%s", p.stderr[-5000:])
    return p


def ollama_ok() -> bool:
    try:
        requests.get(f"{OLLAMA}/api/tags", timeout=2)
        return True
    except Exception:
        return False


def list_models():
    try:
        return [m["name"] for m in requests.get(f"{OLLAMA}/api/tags", timeout=3).json().get("models", [])]
    except Exception:
        return []


def ensure_ollama_installed():
    if shutil.which("ollama"):
        return True, ""

    logger.warning("Ollama 未検出。初回セットアップを試行します。")
    if shutil.which("brew"):
        try:
            p = _run_logged(["brew", "install", "ollama"], timeout=3600)
            OLLAMA_SETUP_FLAG.write_text(datetime.now().isoformat(), encoding="utf-8")
            if p.returncode == 0 and shutil.which("ollama"):
                return True, ""
        except Exception:
            logger.exception("brew での Ollama インストール失敗")

    installer_url = "https://ollama.com/download/Ollama-darwin.zip"
    try:
        webbrowser.open(installer_url)
        logger.info("Ollama インストーラURLを開きました: %s", installer_url)
    except Exception:
        logger.exception("インストーラURLオープン失敗")

    msg = (
        "Ollama が見つかりません。初回セットアップを試みましたが完了できませんでした。\n"
        f"ブラウザでインストーラを開いてください: {installer_url}\n"
        f"ログ保存先: {STARTUP_LOG_PATH}"
    )
    return False, msg


def ensure_ollama_running(retry_seconds: int = 30):
    if ollama_ok():
        logger.info("Ollama 疎通確認: OK")
        return True, ""

    ollama_bin = shutil.which("ollama")
    if not ollama_bin:
        ok, err = ensure_ollama_installed()
        if not ok:
            return False, err
        ollama_bin = shutil.which("ollama")
        if not ollama_bin:
            return False, f"Ollama インストール後もコマンドが見つかりません。\nログ保存先: {STARTUP_LOG_PATH}"

    try:
        with open(STARTUP_LOG_PATH, "a", encoding="utf-8") as lf:
            p = subprocess.Popen([ollama_bin, "serve"], stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
        logger.info("Ollama起動コマンド実行結果: pid=%s", p.pid)
    except Exception as e:
        logger.exception("ollama serve 起動失敗")
        return False, f"Ollama の自動起動に失敗しました: {e}\nログ保存先: {STARTUP_LOG_PATH}"

    for _ in range(retry_seconds):
        if ollama_ok():
            logger.info("Ollama 起動待ち: OK")
            return True, ""
        time.sleep(1)

    return False, f"Ollama 起動待ちがタイムアウトしました。\nログ保存先: {STARTUP_LOG_PATH}"


def ensure_model_ready():
    if MODEL_NAME in list_models():
        MODEL_READY_FLAG.write_text(datetime.now().isoformat(), encoding="utf-8")
        logger.info("モデル準備: 既に登録済み (%s)", MODEL_NAME)
        return True, ""

    model_dir = bundled_model_dir()
    modelfile = model_dir / "Modelfile"
    if not modelfile.exists():
        return False, f"同梱モデルが見つかりません: {modelfile}\nログ保存先: {STARTUP_LOG_PATH}"

    if MODEL_READY_FLAG.exists():
        return False, f"モデル『{MODEL_NAME}』が未登録ですが初回自動準備は実施済みです。\nログ保存先: {STARTUP_LOG_PATH}"

    try:
        p = _run_logged(["ollama", "create", MODEL_NAME, "-f", str(modelfile)], cwd=str(model_dir), timeout=3600)
        if p.returncode != 0:
            return False, f"モデル準備に失敗しました。\nログ保存先: {STARTUP_LOG_PATH}"
    except Exception as e:
        logger.exception("モデル準備失敗")
        return False, f"モデル準備でエラーが発生しました: {e}\nログ保存先: {STARTUP_LOG_PATH}"

    if MODEL_NAME in list_models():
        MODEL_READY_FLAG.write_text(datetime.now().isoformat(), encoding="utf-8")
        logger.info("モデル準備: 完了 (%s)", MODEL_NAME)
        return True, ""

    return False, f"モデル準備後も登録確認できませんでした。\nログ保存先: {STARTUP_LOG_PATH}"


def get_db():
    if not hasattr(_tlocal, "c"):
        _tlocal.c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _tlocal.c.row_factory = sqlite3.Row
    return _tlocal.c


def init_db():
    c = sqlite3.connect(str(DB_PATH))
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, title TEXT, model TEXT, created_at TEXT, last_active TEXT);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        INSERT OR IGNORE INTO settings VALUES('system_prompt','');
        INSERT OR IGNORE INTO settings VALUES('context_length','20');
        INSERT OR IGNORE INTO settings VALUES('temperature','0.7');
        """
    )
    c.commit()
    c.close()


def dbg(k):
    r = get_db().execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return r["value"] if r else None


def stream_ollama(model, messages, system, temp=0.7):
    resp = requests.post(
        f"{OLLAMA}/api/chat",
        json={"model": model, "messages": messages, "stream": True, "options": {"temperature": temp}, "system": system},
        stream=True,
        timeout=300,
    )
    resp.raise_for_status()
    for line in resp.iter_lines():
        if line:
            d = json.loads(line)
            yield d.get("message", {}).get("content", ""), d.get("done", False)


HTML = """<!DOCTYPE html>
<html lang='ja'><head><meta charset='UTF-8'><title>NEXUS PRO</title></head>
<body style='background:#06060d;color:#dde2f8;font-family:sans-serif'>
<h2>NEXUS PRO</h2><p>localhost:7100 で起動中</p>
</body></html>"""


@flask_app.route("/")
def index():
    return HTML


@flask_app.route("/api/status")
def status():
    return jsonify({"ollama": ollama_ok(), "online": True, "models": list_models() if ollama_ok() else []})


@flask_app.route("/api/chat", methods=["POST"])
def chat():
    d = request.json or {}
    msg = d.get("message", "").strip()
    model = d.get("model", "")
    if not (msg and model):
        return jsonify({"error": "message/model が必要"}), 400
    if not ollama_ok():
        return jsonify({"error": f"Ollama が起動していません。\nログ保存先: {STARTUP_LOG_PATH}"}), 503

    def gen():
        full = ""
        try:
            for chunk, done in stream_ollama(model, [{"role": "user", "content": msg}], "", float(dbg("temperature") or 0.7)):
                full += chunk
                yield f"data: {json.dumps({'text': chunk})}\n\n"
                if done:
                    break
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(stream_with_context(gen()), mimetype="text/event-stream")


def run_server():
    init_db()
    logger.info("Flask起動（7100）")
    flask_app.run(host="127.0.0.1", port=APP_PORT, debug=False, threaded=True, use_reloader=False)


def fail_and_exit(msg: str):
    print(msg)
    print(f"ログ保存先: {STARTUP_LOG_PATH}")
    logger.error(msg)
    sys.exit(1)


def main():
    logger.info("NEXUS PRO 起動開始")

    if _port_in_use(APP_PORT):
        fail_and_exit(f"ポート {APP_PORT} はすでに使用されています。別プロセスを停止後に再実行してください。")

    ok, err = ensure_ollama_running()
    if not ok:
        fail_and_exit(err)

    ok, err = ensure_model_ready()
    if not ok:
        fail_and_exit(err)

    t = threading.Thread(target=run_server, daemon=True)
    t.start()

    url = f"http://127.0.0.1:{APP_PORT}"
    if not _wait_server(APP_PORT, timeout=30):
        fail_and_exit("Flask 起動待ちがタイムアウトしました。")

    logger.info("起動完了: %s", url)
    print(f"[NEXUS PRO] 起動完了: {url}")

    try:
        import webview

        webview.create_window(
            "NEXUS PRO",
            url,
            width=1300,
            height=860,
            min_size=(920, 640),
            resizable=True,
            text_select=True,
            zoomable=True,
            confirm_close=True,
            background_color="#06060d",
        )
        webview.start(gui="cocoa", private_mode=False, storage_path=APP_SUPPORT)
    except ImportError:
        logger.warning("pywebview 未インストール。ブラウザで開きます。")
        webbrowser.open(url)
        while True:
            time.sleep(1)
    except Exception:
        logger.exception("WebView エラー。ブラウザでフォールバック。")
        webbrowser.open(url)
        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()
