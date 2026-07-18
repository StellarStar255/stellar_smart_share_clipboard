#!/usr/bin/env python3
"""Stellar Smart Share Clipboard (Qt 版)

在同一局域网内的多台电脑 (macOS / Windows / Linux) 之间同步剪贴板,
支持 文本 + 图片, 带主窗口界面和系统托盘图标。

用法: 在每台电脑上运行
    python clipboard_share_qt.py [--secret 口令]

原理:
  - QClipboard 事件驱动地监听本机剪贴板变化 (无需轮询)
  - UDP 广播 (端口 48765) 自动发现同网段的其他实例
  - 剪贴板变化时通过 TCP (端口 48766) 推送给所有已知节点
  - 消息带 HMAC 校验, 所有机器必须使用相同 --secret

依赖: pip install PySide6
"""

import argparse
import base64
import hashlib
import hmac
import json
import socket
import struct
import sys
import threading
import time
import uuid

from PySide6.QtCore import QBuffer, QIODevice, QObject, Qt, Signal
from PySide6.QtGui import QAction, QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel,
                               QListWidget, QMenu, QPlainTextEdit,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

DISCOVERY_PORT = 48765
TRANSFER_PORT = 48766
ANNOUNCE_INTERVAL = 2.0
PEER_TIMEOUT = 10.0
MAX_PAYLOAD = 64 * 1024 * 1024  # 图片可能较大, 上限 64MB
MAGIC = b"SSSC"

NODE_ID = uuid.uuid4().hex


def sign(secret: str, data: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), data, hashlib.sha256).hexdigest()


def build_packet(secret: str, payload: bytes) -> bytes:
    return (MAGIC + sign(secret, payload).encode("ascii")
            + struct.pack("!I", len(payload)) + payload)


def recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError("连接中断")
        buf += chunk
    return buf


class Bridge(QObject):
    """把网络线程的事件转交给 GUI 主线程 (剪贴板和界面只能在主线程操作)。"""
    remote_clip = Signal(dict)      # {"type": "text"/"image", "data": str}
    peers_changed = Signal(list)    # 在线节点 IP 列表
    status = Signal(str)            # 日志消息


class SyncEngine:
    def __init__(self, secret: str, bridge: Bridge):
        self.secret = secret
        self.bridge = bridge
        self.lock = threading.Lock()
        self.peers = {}          # node_id -> (ip, last_seen)
        self.last_hash = None    # 最近同步内容的哈希, 防回环
        self.paused = False

    def start(self):
        """先在主线程绑定端口, 失败时抛出带清晰提示的异常。"""
        try:
            self._disc_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._disc_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._disc_sock.bind(("", DISCOVERY_PORT))
            self._srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._srv_sock.bind(("", TRANSFER_PORT))
            self._srv_sock.listen(8)
        except OSError as e:
            raise RuntimeError(
                f"端口被占用 (UDP {DISCOVERY_PORT} / TCP {TRANSFER_PORT})。\n"
                f"本机可能已经运行了一个剪贴板同步实例, 请先关闭它。\n\n{e}")
        for target in (self._announce_loop, self._discovery_loop,
                       self._server_loop):
            threading.Thread(target=target, daemon=True).start()

    def peer_ips(self):
        with self.lock:
            return [ip for ip, _ in self.peers.values()]

    # ---- 发现 ----

    def _announce_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        payload = json.dumps({"id": NODE_ID}).encode("utf-8")
        packet = MAGIC + sign(self.secret, payload).encode("ascii") + payload
        while True:
            try:
                sock.sendto(packet, ("255.255.255.255", DISCOVERY_PORT))
            except OSError:
                pass
            now = time.time()
            with self.lock:
                dead = [nid for nid, (_, seen) in self.peers.items()
                        if now - seen > PEER_TIMEOUT]
                for nid in dead:
                    ip = self.peers.pop(nid)[0]
                    self.bridge.status.emit(f"节点下线: {ip}")
                ips = [ip for ip, _ in self.peers.values()]
            if dead:
                self.bridge.peers_changed.emit(ips)
            time.sleep(ANNOUNCE_INTERVAL)

    def _discovery_loop(self):
        sock = self._disc_sock
        while True:
            try:
                data, addr = sock.recvfrom(4096)
            except OSError:
                continue
            if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 64:
                continue
            sig = data[len(MAGIC):len(MAGIC) + 64].decode("ascii", "ignore")
            payload = data[len(MAGIC) + 64:]
            if not hmac.compare_digest(sig, sign(self.secret, payload)):
                continue
            try:
                info = json.loads(payload)
            except ValueError:
                continue
            nid = info.get("id")
            if not nid or nid == NODE_ID:
                continue
            with self.lock:
                is_new = nid not in self.peers
                self.peers[nid] = (addr[0], time.time())
                ips = [ip for ip, _ in self.peers.values()]
            if is_new:
                self.bridge.status.emit(f"发现节点: {addr[0]}")
                self.bridge.peers_changed.emit(ips)

    # ---- 接收 ----

    def _server_loop(self):
        srv = self._srv_sock
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=self._handle_incoming,
                             args=(conn, addr), daemon=True).start()

    def _handle_incoming(self, conn: socket.socket, addr):
        try:
            with conn:
                conn.settimeout(60)
                header = recv_exact(conn, len(MAGIC) + 64 + 4)
                if not header.startswith(MAGIC):
                    return
                sig = header[len(MAGIC):len(MAGIC) + 64].decode("ascii", "ignore")
                (length,) = struct.unpack("!I", header[len(MAGIC) + 64:])
                if length > MAX_PAYLOAD:
                    return
                payload = recv_exact(conn, length)
                if not hmac.compare_digest(sig, sign(self.secret, payload)):
                    return
                msg = json.loads(payload)
                if msg.get("id") == NODE_ID:
                    return
                if msg.get("type") not in ("text", "image") \
                        or not isinstance(msg.get("data"), str):
                    return
                if self.paused:
                    return
                h = hashlib.sha256(msg["data"].encode("utf-8")).hexdigest()
                with self.lock:
                    if h == self.last_hash:
                        return
                    self.last_hash = h
                self.bridge.remote_clip.emit(msg)
                kind = "图片" if msg["type"] == "image" else "文本"
                self.bridge.status.emit(f"收到{kind} 来自 {addr[0]}")
        except (ConnectionError, socket.timeout, ValueError, OSError):
            pass

    # ---- 发送 ----

    def broadcast(self, kind: str, data: str):
        """kind: 'text' 或 'image'(PNG base64)。在主线程调用, 网络发送在子线程。"""
        if self.paused:
            return
        h = hashlib.sha256(data.encode("utf-8")).hexdigest()
        with self.lock:
            if h == self.last_hash:
                return  # 是我们自己刚设置的内容, 跳过
            self.last_hash = h
            targets = [ip for ip, _ in self.peers.values()]
        if not targets:
            return
        payload = json.dumps(
            {"id": NODE_ID, "type": kind, "data": data}).encode("utf-8")
        packet = build_packet(self.secret, payload)
        name = "图片" if kind == "image" else "文本"
        self.bridge.status.emit(f"推送{name} 到 {len(targets)} 个节点")
        for ip in targets:
            threading.Thread(target=self._send_to_peer, args=(ip, packet),
                             daemon=True).start()

    def _send_to_peer(self, ip: str, packet: bytes):
        try:
            with socket.create_connection((ip, TRANSFER_PORT), timeout=10) as c:
                c.sendall(packet)
        except OSError as e:
            self.bridge.status.emit(f"发送到 {ip} 失败: {e}")


def make_app_icon() -> QPixmap:
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#4A90D9"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(8, 4, 48, 56, 8, 8)
    p.setBrush(QColor("white"))
    p.drawRoundedRect(20, 0, 24, 12, 4, 4)
    p.drawRect(16, 20, 32, 4)
    p.drawRect(16, 30, 32, 4)
    p.drawRect(16, 40, 20, 4)
    p.end()
    return pix


class MainWindow(QWidget):
    """主窗口: 状态 / 在线节点 / 同步记录。关闭时隐藏到托盘, 不退出。"""

    def __init__(self, engine: SyncEngine, secret: str):
        super().__init__()
        self.engine = engine
        self.setWindowTitle("Stellar 剪贴板同步")
        self.setWindowIcon(make_app_icon())
        self.resize(420, 480)

        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.status_label = QLabel("● 运行中")
        self.status_label.setStyleSheet("color: #2E9E44; font-weight: bold;")
        header.addWidget(self.status_label)
        header.addStretch()
        header.addWidget(QLabel(f"本机节点 {NODE_ID[:8]}"))
        layout.addLayout(header)

        info = QLabel(f"口令: {secret}    端口: UDP {DISCOVERY_PORT} / "
                      f"TCP {TRANSFER_PORT}")
        info.setStyleSheet("color: gray;")
        layout.addWidget(info)

        self.pause_box = QCheckBox("暂停同步")
        self.pause_box.toggled.connect(self.on_pause_toggled)
        layout.addWidget(self.pause_box)

        self.peer_label = QLabel("在线节点 (0):")
        layout.addWidget(self.peer_label)
        self.peer_list = QListWidget()
        self.peer_list.setMaximumHeight(110)
        layout.addWidget(self.peer_list)

        layout.addWidget(QLabel("同步记录:"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        layout.addWidget(self.log_view)

        hint = QLabel("关闭窗口将最小化到系统托盘继续运行")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(hint)

    def on_pause_toggled(self, checked: bool):
        self.engine.paused = checked
        if checked:
            self.status_label.setText("‖ 已暂停")
            self.status_label.setStyleSheet(
                "color: #C77700; font-weight: bold;")
            self.append_log("同步已暂停")
        else:
            self.status_label.setText("● 运行中")
            self.status_label.setStyleSheet(
                "color: #2E9E44; font-weight: bold;")
            self.append_log("同步已恢复")

    def update_peers(self, ips: list):
        self.peer_label.setText(f"在线节点 ({len(ips)}):")
        self.peer_list.clear()
        self.peer_list.addItems(ips)

    def append_log(self, message: str):
        self.log_view.appendPlainText(
            f"[{time.strftime('%H:%M:%S')}] {message}")

    def closeEvent(self, event):
        event.ignore()
        self.hide()


class App:
    def __init__(self, secret: str):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.clipboard = self.app.clipboard()

        self.bridge = Bridge()
        self.engine = SyncEngine(secret, self.bridge)
        self.window = MainWindow(self.engine, secret)

        self.bridge.remote_clip.connect(self.apply_remote, Qt.QueuedConnection)
        self.bridge.peers_changed.connect(self.on_peers_changed)
        self.bridge.status.connect(self.window.append_log)

        icon = make_app_icon()
        self.tray = QSystemTrayIcon(icon)
        menu = QMenu()
        show_action = QAction("显示主窗口", menu)
        show_action.triggered.connect(self.show_window)
        menu.addAction(show_action)
        self.peer_action = QAction("节点: 0 在线", menu)
        self.peer_action.setEnabled(False)
        menu.addAction(self.peer_action)
        menu.addSeparator()
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.app.quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("Stellar 剪贴板同步 — 0 节点在线")
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

        self.clipboard.dataChanged.connect(self.on_local_change)

    def show_window(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def on_local_change(self):
        mime = self.clipboard.mimeData()
        if mime is None:
            return
        if mime.hasImage():
            image = self.clipboard.image()
            if image.isNull():
                return
            buf = QBuffer()
            buf.open(QIODevice.WriteOnly)
            image.save(buf, "PNG")
            data = base64.b64encode(bytes(buf.data())).decode("ascii")
            self.engine.broadcast("image", data)
        elif mime.hasText():
            text = mime.text()
            if text:
                self.engine.broadcast("text", text)

    def apply_remote(self, msg: dict):
        if msg["type"] == "text":
            self.clipboard.setText(msg["data"])
        else:
            raw = base64.b64decode(msg["data"])
            image = QImage.fromData(raw, "PNG")
            if not image.isNull():
                self.clipboard.setImage(image)

    def on_peers_changed(self, ips: list):
        self.window.update_peers(ips)
        self.peer_action.setText(f"节点: {len(ips)} 在线")
        self.tray.setToolTip(f"Stellar 剪贴板同步 — {len(ips)} 节点在线")

    def run(self) -> int:
        try:
            self.engine.start()
        except RuntimeError as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "无法启动", str(e))
            return 1
        self.window.show()
        self.window.append_log(f"已启动, 本机节点 {NODE_ID[:8]}")
        self.window.append_log("等待发现同网段节点…")
        return self.app.exec()


def main():
    parser = argparse.ArgumentParser(description="局域网剪贴板同步 (Qt 版)")
    parser.add_argument("--secret", default="stellar-clipboard",
                        help="共享口令, 所有机器必须一致 (默认: stellar-clipboard)")
    args = parser.parse_args()
    sys.exit(App(args.secret).run())


if __name__ == "__main__":
    main()
