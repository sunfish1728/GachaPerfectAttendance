"""程式進入點：python -m gachahub"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .core.config import Paths

def instance_key(paths: Paths) -> str:
    """單一實例通道名稱：依資料夾區分，開發用與正式使用的實例互不干擾。"""
    import hashlib

    digest = hashlib.sha1(str(paths.root.resolve()).lower().encode("utf-8")).hexdigest()[:10]
    return f"gachahub-{digest}"


def setup_logging(paths: Paths) -> None:
    handler = RotatingFileHandler(paths.logs / "gachahub.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication

    paths = Paths()
    paths.ensure()
    setup_logging(paths)

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("二游腳本集合站")
    app.setQuitOnLastWindowClosed(False)  # 縮到托盤時不結束
    from .gui.i18n import install_translator

    install_translator(app)

    # 單一實例：已有實例在跑就請它顯示視窗，然後自己結束
    sock = QLocalSocket()
    key = instance_key(paths)
    sock.connectToServer(key)
    if sock.waitForConnected(500):
        # 由工作排程器喚醒啟動（--minimized）時只確認已在執行，不打擾使用者
        sock.write(b"ping" if "--minimized" in sys.argv else b"show")
        sock.waitForBytesWritten(500)
        sock.disconnectFromServer()
        return 0

    from .gui.controller import AppController
    from .gui.elevation import is_admin, relaunch_as_admin
    from .gui.main_window import MainWindow
    from .gui.settings_page import cfg, load_config

    load_config(paths.root / "data" / "settings.json")
    # 設定為自動提權時，未提權的實例改以管理員身分重開；使用者拒絕 UAC 則照常以一般權限執行
    if cfg.autoElevate.value and not is_admin() and "--elevated" not in sys.argv:
        extra = ["--minimized"] if "--minimized" in sys.argv else []
        if relaunch_as_admin(extra):
            return 0

    controller = AppController(paths)
    window = MainWindow(controller)
    app.setWindowIcon(window.app_icon)  # 對話框、倒數提示等其他視窗也使用同一圖示

    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.WorldAccessOption)  # 允許未提權的實例連到提權的實例
    QLocalServer.removeServer(key)
    server.listen(key)
    window.instance_key = key
    def on_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is None:
            return
        conn.waitForReadyRead(300)
        if bytes(conn.readAll().data()) != b"ping":
            window.bring_to_front()
        conn.disconnectFromServer()

    server.newConnection.connect(on_connection)
    window.instance_server = server  # 提權重開前需先關閉，否則新實例會把自己當成重複實例

    if "--minimized" not in sys.argv:
        window.show()
    logging.getLogger("gachahub").info("啟動，資料位置：%s，管理員：%s", paths.root, is_admin())
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
