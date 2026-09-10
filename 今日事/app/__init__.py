# -*- coding: utf-8 -*-
"""今日事 - 日历待办清单应用"""
import sys
from pathlib import Path

from flask import Flask

from .database import init_db
from .routes import api_bp


def _resource_root() -> Path:
    """模板/静态资源目录：打包后位于 PyInstaller 解压目录，源码运行位于项目根"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


ROOT = _resource_root()


def create_app():
    app = Flask(
        __name__,
        template_folder=str(ROOT / "templates"),
        static_folder=str(ROOT / "static"),
    )
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # 静态文件每次校验更新，避免缓存旧版
    app.config["TEMPLATES_AUTO_RELOAD"] = True   # 模板修改即时生效
    init_db()
    app.register_blueprint(api_bp)
    return app
