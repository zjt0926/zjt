# -*- coding: utf-8 -*-
"""今日事 启动入口：启动本地服务并自动打开浏览器。
打包为 exe 后双击即可运行，无需命令行窗口。
"""
import os
import socket
import sys
import threading
import urllib.request
import webbrowser

# 无控制台打包模式下 sys.stdout/stderr 为 None，重定向以避免日志写入报错
if getattr(sys, "frozen", False):
    _devnull = open(os.devnull, "w")
    sys.stdout = _devnull
    sys.stderr = _devnull

from app import create_app  # noqa: E402

HOST = "127.0.0.1"


def _port_alive(port: int) -> bool:
    """该端口是否已有一个今日事实例在运行"""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/tags", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def _pick_port():
    """返回可用端口；若端口被已有实例占用，返回 ('running', port)"""
    for port in range(5001, 5021):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                if _port_alive(port):
                    return ("running", port)
    return 5001


def main():
    result = _pick_port()
    if isinstance(result, tuple):  # 已有实例在运行：直接打开浏览器并退出
        webbrowser.open(f"http://{HOST}:{result[1]}")
        return

    port = result
    url = f"http://{HOST}:{port}"
    app = create_app()
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    # 静默 werkzeug 启动横幅（无控制台模式）
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    app.run(host=HOST, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
