#!/usr/bin/env python3
"""Stellar Smart Share Clipboard (Qt 版)

在同一局域网内的多台电脑 (macOS / Windows / Linux) 之间同步剪贴板,
支持 文本 + 图片, 带主窗口界面和系统托盘图标。

用法: 在每台电脑上运行
    python clipboard_share_qt.py --secret 口令
  或用环境变量传口令 (不进 shell 历史/进程列表):
    SSSC_SECRET=口令 python clipboard_share_qt.py
  两者都没有时会提示交互输入。

原理:
  - QClipboard 事件驱动地监听本机剪贴板变化 (无需轮询)
  - UDP 广播 (端口 48765) 自动发现同网段的其他实例
  - 剪贴板变化时通过 TCP (端口 48766) 推送给所有已知节点
  - 所有消息用 ChaCha20-Poly1305 加密 (密钥由 --secret 经 scrypt 派生),
    并带时间戳 + nonce 防重放, 所有机器必须使用相同 --secret

依赖: pip install PySide6 cryptography
"""

import argparse
import getpass
import hashlib
import os
import queue
import socket
import struct
import sys
import threading
import time
import uuid

try:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
except ImportError:
    sys.exit("缺少依赖 cryptography, 请先执行: pip install cryptography")

from PySide6.QtCore import (QBuffer, QIODevice, QObject, Qt, QTimer, Signal)
from PySide6.QtGui import QAction, QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel,
                               QListWidget, QMenu, QPlainTextEdit,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

DISCOVERY_PORT = 48765
TRANSFER_PORT = 48766
ANNOUNCE_INTERVAL = 2.0
PEER_TIMEOUT = 10.0
MAX_PAYLOAD = 64 * 1024 * 1024  # 图片可能较大, 上限 64MB
MAGIC = b"SSC2"                 # 协议 v2 (加密), 与旧版明文协议不兼容
KDF_SALT = b"stellar-smart-share-clipboard-v2"
NONCE_LEN = 12
TIME_WINDOW = 30.0              # 消息时间戳容忍偏差 (秒), 防重放
MAX_CONNECTIONS = 8             # TCP 并发接收上限, 防内存耗尽
SEND_QUEUE_MAX = 16             # 每节点待发送队列上限, 满时丢弃最旧内容

NODE = uuid.uuid4().bytes       # 本机节点标识 (16 字节)
NODE_ID = NODE.hex()

# 每条消息明文的公共头: 时间戳 (double) + 节点标识 (16 字节)
HEADER = struct.Struct("!d16s")

KIND_TEXT = 0
KIND_IMAGE = 1


def derive_key(secret: str) -> bytes:
    return hashlib.scrypt(secret.encode("utf-8"), salt=KDF_SALT,
                          n=2 ** 14, r=8, p=1, dklen=32)


def recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError("连接中断")
        buf += chunk
    return bytes(buf)


class Bridge(QObject):
    """把网络线程的事件转交给 GUI 主线程 (剪贴板和界面只能在主线程操作)。"""
    remote_clip = Signal(dict)      # {"type": "text"/"image", "data": str}
    peers_changed = Signal(list)    # 在线节点 IP 列表
    status = Signal(str)            # 日志消息


class SyncEngine:
    def __init__(self, secret: str, bridge: Bridge):
        self.bridge = bridge
        self.cipher = ChaCha20Poly1305(derive_key(secret))
        self.lock = threading.Lock()
        self.peers = {}          # node_id -> (ip, last_seen)
        self.last_hash = None    # 最近同步内容的哈希, 防回环
        self.paused = False
        self._nonces = {}        # nonce -> seen_at, 防重放
        self._skew_warned = 0.0  # 上次时钟偏差告警时间, 避免刷屏
        self._out_q = queue.Queue()   # 待编码/加密/推送的本地剪贴板内容
        self._senders = {}            # ip -> 发送队列, 每节点一个按序发送线程
        self._conn_slots = threading.Semaphore(MAX_CONNECTIONS)

    # ---- 加密 ----

    def _seal(self, plaintext: bytes) -> bytes:
        nonce = os.urandom(NONCE_LEN)
        return nonce + self.cipher.encrypt(nonce, plaintext, MAGIC)

    def _open_checked(self, blob: bytes):
        """解密 + 时间戳/nonce 防重放校验。返回 (node, body) 或 None。"""
        if len(blob) < NONCE_LEN + 16 + HEADER.size:
            return None
        nonce = bytes(blob[:NONCE_LEN])
        try:
            plain = self.cipher.decrypt(nonce, bytes(blob[NONCE_LEN:]), MAGIC)
        except Exception:
            return None  # 口令不同或数据被篡改
        ts, node = HEADER.unpack_from(plain)
        if node == NODE:
            return None
        now = time.time()
        if abs(now - ts) > TIME_WINDOW:
            if now - self._skew_warned > 60:
                self._skew_warned = now
                self.bridge.status.emit(
                    "忽略了时间戳偏差过大的消息, 请检查各机器的系统时间是否同步")
            return None
        with self.lock:
            if nonce in self._nonces:
                return None  # 重放
            self._nonces[nonce] = now
            expired = [n for n, seen in self._nonces.items()
                       if now - seen > TIME_WINDOW * 2]
            for n in expired:
                del self._nonces[n]
        return node, plain[HEADER.size:]

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
                       self._server_loop, self._dispatch_loop):
            threading.Thread(target=target, daemon=True).start()

    def peer_ips(self):
        with self.lock:
            return [ip for ip, _ in self.peers.values()]

    # ---- 发现 ----

    def _announce_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while True:
            # 每次重新加密: 时间戳和 nonce 都必须是新的
            packet = MAGIC + self._seal(HEADER.pack(time.time(), NODE))
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
                time.sleep(0.5)  # 避免 socket 异常时空转
                continue
            if not data.startswith(MAGIC):
                continue
            opened = self._open_checked(data[len(MAGIC):])
            if opened is None:
                continue
            node, _ = opened
            with self.lock:
                is_new = node not in self.peers
                self.peers[node] = (addr[0], time.time())
                ips = [ip for ip, _ in self.peers.values()]
            if is_new:
                self.bridge.status.emit(f"发现节点: {addr[0]}")
                self.bridge.peers_changed.emit(ips)

    # ---- 接收 ----

    def _server_loop(self):
        srv = self._srv_sock
        while True:
            conn, addr = srv.accept()
            with self.lock:
                known = any(ip == addr[0] for ip, _ in self.peers.values())
            # 只接受已通过发现认证的节点 IP, 并限制并发连接数;
            # 陌生 IP 无法让本机读入任何数据 (防未认证的内存 DoS)
            if not known or not self._conn_slots.acquire(blocking=False):
                conn.close()
                continue
            threading.Thread(target=self._handle_incoming,
                             args=(conn, addr), daemon=True).start()

    def _handle_incoming(self, conn: socket.socket, addr):
        try:
            with conn:
                conn.settimeout(60)
                header = recv_exact(conn, len(MAGIC) + 4)
                if not header.startswith(MAGIC):
                    return
                (length,) = struct.unpack("!I", header[len(MAGIC):])
                if length > MAX_PAYLOAD + 512:  # 密文比明文多 nonce/tag/头部
                    return
                blob = recv_exact(conn, length)
                opened = self._open_checked(blob)
                if opened is None:
                    return
                _, body = opened
                if len(body) < 1 or body[0] not in (KIND_TEXT, KIND_IMAGE):
                    return
                if self.paused:
                    return
                data = body[1:]
                h = hashlib.sha256(data).digest()
                with self.lock:
                    if h == self.last_hash:
                        return
                    self.last_hash = h
                is_image = body[0] == KIND_IMAGE
                self.bridge.remote_clip.emit(
                    {"type": "image" if is_image else "text", "data": data})
                self.bridge.status.emit(
                    f"收到{'图片' if is_image else '文本'} 来自 {addr[0]}")
        except (ConnectionError, socket.timeout, ValueError, OSError):
            pass
        finally:
            self._conn_slots.release()

    # ---- 发送 ----

    def submit(self, kind: int, obj):
        """kind: KIND_TEXT (obj 为 str) / KIND_IMAGE (obj 为 QImage)。
        在主线程调用; 编码/加密/发送都在工作线程完成, 不阻塞界面。"""
        self._out_q.put((kind, obj))

    def _dispatch_loop(self):
        """单线程串行处理本地剪贴板内容, 保证先复制的先送达。"""
        while True:
            kind, obj = self._out_q.get()
            if self.paused:
                continue
            if kind == KIND_IMAGE:
                buf = QBuffer()
                buf.open(QIODevice.WriteOnly)
                obj.save(buf, "PNG")
                data = bytes(buf.data())
                if not data:
                    continue
            else:
                data = obj.encode("utf-8")
            h = hashlib.sha256(data).digest()
            with self.lock:
                if h == self.last_hash:
                    continue  # 是我们自己刚设置的内容, 跳过
                self.last_hash = h
                targets = [ip for ip, _ in self.peers.values()]
            if not targets:
                continue
            blob = self._seal(HEADER.pack(time.time(), NODE)
                              + bytes([kind]) + data)
            packet = MAGIC + struct.pack("!I", len(blob)) + blob
            name = "图片" if kind == KIND_IMAGE else "文本"
            self.bridge.status.emit(f"推送{name} 到 {len(targets)} 个节点")
            for ip in targets:
                self._enqueue_send(ip, packet)

    def _enqueue_send(self, ip: str, packet: bytes):
        with self.lock:
            q = self._senders.get(ip)
            if q is None:
                q = queue.Queue(maxsize=SEND_QUEUE_MAX)
                self._senders[ip] = q
                threading.Thread(target=self._send_loop, args=(ip, q),
                                 daemon=True).start()
        while True:
            try:
                q.put_nowait(packet)
                return
            except queue.Full:
                try:
                    q.get_nowait()  # 节点长时间不可达时丢弃最旧的内容
                except queue.Empty:
                    pass

    def _send_loop(self, ip: str, q: queue.Queue):
        while True:
            packet = q.get()
            try:
                with socket.create_connection((ip, TRANSFER_PORT),
                                              timeout=10) as c:
                    c.sendall(packet)
            except OSError as e:
                self.bridge.status.emit(f"发送到 {ip} 失败: {e}")


ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "assets", "stellar_smart_share_clipboard.png")


def make_app_icon() -> QPixmap:
    pix = QPixmap(ICON_PATH)
    if not pix.isNull():
        return pix
    # 图标文件缺失时回退到程序绘制的图标
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
        self.resize(420, 480)

        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.status_label = QLabel("● 运行中")
        self.status_label.setStyleSheet("color: #2E9E44; font-weight: bold;")
        header.addWidget(self.status_label)
        header.addStretch()
        header.addWidget(QLabel(f"本机节点 {NODE_ID[:8]}"))
        layout.addLayout(header)

        masked = (secret[:2] + "••••••") if len(secret) > 2 else "••••••"
        info = QLabel(f"口令: {masked}    端口: UDP {DISCOVERY_PORT} / "
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
        icon = make_app_icon()
        self.app.setWindowIcon(icon)  # 主窗口随 QApplication 继承此图标
        self.app.setQuitOnLastWindowClosed(False)
        self.clipboard = self.app.clipboard()
        self._applying = False  # 正在把远端内容写入剪贴板, 抑制回环广播

        self.bridge = Bridge()
        self.engine = SyncEngine(secret, self.bridge)
        self.window = MainWindow(self.engine, secret)

        self.bridge.remote_clip.connect(self.apply_remote, Qt.QueuedConnection)
        self.bridge.peers_changed.connect(self.on_peers_changed)
        self.bridge.status.connect(self.window.append_log)

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
        if self._applying:
            return  # 剪贴板变化来自远端同步, 不再广播回去
        mime = self.clipboard.mimeData()
        if mime is None:
            return
        if mime.hasImage():
            image = self.clipboard.image()
            if not image.isNull():
                self.engine.submit(KIND_IMAGE, image)  # PNG 编码在工作线程
        elif mime.hasText():
            text = mime.text()
            if text:
                self.engine.submit(KIND_TEXT, text)

    def apply_remote(self, msg: dict):
        self._applying = True
        try:
            if msg["type"] == "text":
                self.clipboard.setText(msg["data"].decode("utf-8", "replace"))
            else:
                image = QImage.fromData(msg["data"], "PNG")
                if not image.isNull():
                    self.clipboard.setImage(image)
        finally:
            # dataChanged 可能同步触发, 也可能经事件队列; 用零延时定时器
            # 在这些事件处理完之后再解除抑制
            QTimer.singleShot(0, self._clear_applying)

    def _clear_applying(self):
        self._applying = False

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


def resolve_secret(cli_secret):
    """按 --secret > 环境变量 SSSC_SECRET > 交互输入 的顺序取口令。"""
    secret = cli_secret or os.environ.get("SSSC_SECRET")
    if not secret and sys.stdin is not None and sys.stdin.isatty():
        secret = getpass.getpass("请输入共享口令 (输入不回显): ")
    return secret


def main():
    parser = argparse.ArgumentParser(description="局域网剪贴板同步 (Qt 版)")
    parser.add_argument("--secret",
                        help="共享口令, 所有机器必须一致, 请使用足够复杂的口令。"
                             "为避免泄露到 shell 历史和进程列表, 推荐改用"
                             "环境变量 SSSC_SECRET 或留空后交互输入")
    args = parser.parse_args()
    secret = resolve_secret(args.secret)
    if not secret:
        parser.error("需要口令: 通过 --secret、环境变量 SSSC_SECRET "
                     "或交互输入提供")
    sys.exit(App(secret).run())


if __name__ == "__main__":
    main()
