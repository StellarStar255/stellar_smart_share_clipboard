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
  - 剪贴板变化时通过 TCP (端口 48766) 长连接推送给所有已知节点,
    >=4KB 的文本先 zlib 压缩
  - 所有消息用 ChaCha20-Poly1305 加密 (密钥由 --secret 经 scrypt 派生),
    并带时间戳 + nonce 防重放, 所有机器必须使用相同 --secret
  - 记住的口令存系统钥匙串 (keyring), 无可用后端时回退明文文件

依赖: pip install PySide6 cryptography keyring
"""

import argparse
import getpass
import hashlib
import json
import os
import queue
import select
import signal
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
import zlib

try:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
except ImportError:
    sys.exit("缺少依赖 cryptography, 请先执行: pip install cryptography")

from PySide6.QtCore import (QBuffer, QIODevice, QObject, Qt, QTimer, Signal)
from PySide6.QtGui import QAction, QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog,
                               QDialogButtonBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMenu, QMessageBox,
                               QPlainTextEdit, QSystemTrayIcon, QVBoxLayout,
                               QWidget)

APP_VERSION = "2.1.0"
GITHUB_REPO = "StellarStar255/stellar_smart_share_clipboard"
UPDATE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# PyInstaller 打包的 Python 找不到系统 CA 证书库, 用 certifi 捆绑的证书
try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()

# 口令优先存系统钥匙串 (macOS Keychain / Windows 凭据管理器 / Secret
# Service); keyring 未安装或无可用后端 (如无桌面的 Linux) 时回退明文文件
try:
    import keyring
except ImportError:
    keyring = None

DISCOVERY_PORT = 48765
TRANSFER_PORT = 48766
ANNOUNCE_INTERVAL = 2.0
PEER_TIMEOUT = 10.0
MAX_PAYLOAD = 64 * 1024 * 1024  # 图片可能较大, 上限 64MB
MAGIC = b"SSC2"                 # 协议 v2 (加密), 与旧版明文协议不兼容
KDF_SALT = b"stellar-smart-share-clipboard-v2"
NONCE_LEN = 12
TIME_WINDOW = 30.0              # 消息时间戳容忍偏差 (秒), 防重放
# TCP 并发接收上限, 防内存耗尽; 发送方保持长连接, 每个在线节点长期占用一个
MAX_CONNECTIONS = 16
SEND_QUEUE_MAX = 16             # 每节点待发送队列上限, 满时丢弃最旧内容

NODE = uuid.uuid4().bytes       # 本机节点标识 (16 字节)
NODE_ID = NODE.hex()
HOSTNAME = socket.gethostname()
GOSSIP_TTL = 60.0               # 从其他节点名单里学到的 IP 的尝试时长 (秒)

# 每条消息明文的公共头: 时间戳 (double) + 节点标识 (16 字节)
HEADER = struct.Struct("!d16s")

KIND_TEXT = 0
KIND_IMAGE = 1
KIND_TEXT_Z = 2       # zlib 压缩的文本; 2.0.7 及更早版本不识别, 会静默忽略

COMPRESS_MIN = 4096   # 文本达到此大小才压缩, 小文本保持与旧版本兼容
# Qt PNG 编码的 quality: 越大压缩越低越快, >=90 完全不压缩 (体积爆炸)。
# 实测 80 比默认快约 40% 且体积相当, 见 commit 说明
PNG_QUALITY = 80


def derive_key(secret: str) -> bytes:
    return hashlib.scrypt(secret.encode("utf-8"), salt=KDF_SALT,
                          n=2 ** 14, r=8, p=1, dklen=32)


_bcast_cache = (0.0, set())  # (过期时刻, 地址集), 仅 announce 线程读写


def _local_broadcast_addrs():
    """猜测本机所在子网的定向广播地址 (按 /24), 作为受限广播的补充。
    结果缓存 30 秒, 免得每轮心跳都建 socket 查路由; 换网络最迟 30 秒跟上。"""
    global _bcast_cache
    now = time.monotonic()
    if now < _bcast_cache[0]:
        return _bcast_cache[1]
    addrs = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))  # 不发包, 只查路由拿本机出口 IP
            addrs = {s.getsockname()[0].rsplit(".", 1)[0] + ".255"}
        finally:
            s.close()
    except OSError:
        pass
    _bcast_cache = (now + 30.0, addrs)
    return addrs


def recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError("连接中断")
        buf += chunk
    return bytes(buf)


def discard_exact(conn: socket.socket, n: int):
    """读取并丢弃 n 字节: 暂停同步时既要跳过整个负载 (不解密不缓存),
    又要保持长连接上的流边界对齐。"""
    while n:
        chunk = conn.recv(min(65536, n))
        if not chunk:
            raise ConnectionError("连接中断")
        n -= len(chunk)


def _sock_stale(sock: socket.socket) -> bool:
    """复用长连接前检测对端是否已关闭。协议里接收方从不回传数据,
    socket 变为可读只可能是 FIN/RST, 即连接不可再用。"""
    try:
        readable, _, _ = select.select([sock], [], [], 0)
        return bool(readable)
    except (OSError, ValueError):
        return True


def _safe_decompress(data: bytes):
    """解压 KIND_TEXT_Z 负载; 解压后超过 MAX_PAYLOAD 或数据异常时返回
    None (对端已认证, 主要防实现错误而非恶意压缩炸弹)。"""
    try:
        d = zlib.decompressobj()
        plain = d.decompress(data, MAX_PAYLOAD + 1)
        if len(plain) > MAX_PAYLOAD or not d.eof:
            return None
        return plain
    except zlib.error:
        return None


def _image_fingerprint(img) -> bytes:
    """QImage 原始像素的哈希, 比 PNG 编码快得多, 用于在编码前识别
    重复触发的同一张图 (macOS 一次复制常触发多次 dataChanged)。"""
    h = hashlib.sha256(struct.pack("!iii", img.width(), img.height(),
                                   img.format().value))
    bits = img.constBits()
    if bits is not None:
        h.update(bits)
    return h.digest()


class Bridge(QObject):
    """把网络线程的事件转交给 GUI 主线程 (剪贴板和界面只能在主线程操作)。"""
    # {"type": "text", "data": bytes} 或 {"type": "image", "data": QImage}
    remote_clip = Signal(dict)
    peers_changed = Signal(list)    # 在线节点 IP 列表
    status = Signal(str)            # 日志消息


class SyncEngine:
    def __init__(self, secret: str, bridge: Bridge, manual_peers=()):
        self.bridge = bridge
        self.manual_peers = {ip.strip() for ip in manual_peers if ip.strip()}
        self.cipher = ChaCha20Poly1305(derive_key(secret))
        self.lock = threading.Lock()
        self.peers = {}          # node_id -> (ip, last_seen, hostname)
        self._gossip = {}        # 从节点名单学到的 ip -> expiry
        self.last_hash = None    # 最近同步内容的哈希, 防回环
        self.paused = False
        self._nonces = {}        # nonce -> seen_at, 防重放
        self._skew_warned = 0.0  # 上次时钟偏差告警时间, 避免刷屏
        self._out_q = queue.Queue()   # 待编码/加密/推送的本地剪贴板内容
        self._senders = {}            # ip -> 发送队列, 每节点一个按序发送线程
        self._img_cache = None        # (像素指纹, PNG 数据), 仅 dispatch 线程用
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

    def rekey(self, secret: str):
        """更换口令: 重新派生密钥, 清空节点与防重放状态, 等待重新发现。"""
        with self.lock:
            self.cipher = ChaCha20Poly1305(derive_key(secret))
            self.peers.clear()
            self._nonces.clear()
            self._gossip.clear()
        # peers 已清空, 不会再走 announce 循环的超时下线路径,
        # 在这里回收旧节点的发送线程和长连接 (内部拿锁, 不能在上面锁内调)
        self._retire_dead_senders()
        self.bridge.peers_changed.emit([])
        self.bridge.status.emit("口令已更换, 等待与使用新口令的节点重新配对…")

    def start(self):
        """先在主线程绑定端口, 失败时抛出带清晰提示的异常。"""
        try:
            self._disc_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._disc_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._disc_sock.bind(("", DISCOVERY_PORT))
            self._srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._srv_sock.bind(("", TRANSFER_PORT))
            self._srv_sock.listen(MAX_CONNECTIONS)
        except OSError as e:
            raise RuntimeError(
                f"端口被占用 (UDP {DISCOVERY_PORT} / TCP {TRANSFER_PORT})。\n"
                f"本机可能已经运行了一个剪贴板同步实例, 请先关闭它。\n\n{e}")
        for target in (self._announce_loop, self._discovery_loop,
                       self._server_loop, self._dispatch_loop):
            threading.Thread(target=target, daemon=True).start()

    def _peer_items(self):
        """(ip, hostname) 列表, 供界面显示。调用方需持有 self.lock。"""
        return [(ip, host) for ip, _, host in self.peers.values()]

    # ---- 发现 ----

    def _announce_payload(self) -> bytes:
        """公共头 + JSON 附加信息: 主机名 + 已知节点名单 (gossip)。
        名单让节点互相介绍: A 认识 B/C 时, B/C 能经 A 的名单互相发现。"""
        with self.lock:
            known = sorted({ip for ip, _, _ in self.peers.values()})
        info = {"h": HOSTNAME[:64], "p": known[:16]}
        return (HEADER.pack(time.time(), NODE)
                + json.dumps(info, separators=(",", ":")).encode("utf-8"))

    def _announce_targets(self):
        """广播地址 + 手动节点 + 已知节点 + 名单学来的节点 (单播)。
        部分路由器会抑制/丢弃广播转发, 广播也不能穿越 VPN 和网段;
        对已知/手动节点补发单播, 任一方向通过一次后双方即可稳定互相保活。"""
        now = time.time()
        with self.lock:
            known = {ip for ip, _, _ in self.peers.values()}
            for ip in [ip for ip, exp in self._gossip.items() if exp < now]:
                del self._gossip[ip]
            gossip = set(self._gossip)
        return ({"255.255.255.255"} | _local_broadcast_addrs()
                | self.manual_peers | known | gossip)

    def _announce_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while True:
            # 每次重新加密: 时间戳和 nonce 都必须是新的
            packet = MAGIC + self._seal(self._announce_payload())
            for addr in self._announce_targets():
                try:
                    sock.sendto(packet, (addr, DISCOVERY_PORT))
                except OSError:
                    pass
            now = time.time()
            with self.lock:
                dead = [nid for nid, (_, seen, _) in self.peers.items()
                        if now - seen > PEER_TIMEOUT]
                for nid in dead:
                    ip = self.peers.pop(nid)[0]
                    self.bridge.status.emit(f"节点下线: {ip}")
                items = self._peer_items()
            if dead:
                self.bridge.peers_changed.emit(items)
                self._retire_dead_senders()
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
            node, body = opened
            host, gossip = "", []
            if body:  # 旧版本 (<=2.0.4) 的心跳没有附加信息
                try:
                    info = json.loads(body)
                    host = str(info.get("h", ""))[:64]
                    gossip = [str(x) for x in info.get("p", [])][:16]
                except ValueError:
                    pass
            now = time.time()
            with self.lock:
                is_new = node not in self.peers
                self.peers[node] = (addr[0], now, host)
                mine = {ip for ip, _, _ in self.peers.values()}
                for ip in gossip:
                    if ip not in mine and ip != addr[0]:
                        self._gossip[ip] = now + GOSSIP_TTL
                items = self._peer_items()
            if is_new:
                self.bridge.status.emit(f"发现节点: {host or addr[0]}")
                self.bridge.peers_changed.emit(items)

    # ---- 接收 ----

    def _server_loop(self):
        srv = self._srv_sock
        while True:
            conn, addr = srv.accept()
            with self.lock:
                known = (addr[0] in self.manual_peers
                         or any(ip == addr[0]
                                for ip, _, _ in self.peers.values()))
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
                bad = 0  # 连续解密失败计数
                # 对端复用同一连接连续发送多条消息 (见 _send_loop);
                # 空闲 60 秒超时关闭, 释放连接槽位, 对端下次发送时会重连
                while True:
                    header = recv_exact(conn, len(MAGIC) + 4)
                    if not header.startswith(MAGIC):
                        return
                    (length,) = struct.unpack("!I", header[len(MAGIC):])
                    if length > MAX_PAYLOAD + 512:  # 密文比明文多 nonce/tag/头
                        return
                    if self.paused:
                        discard_exact(conn, length)
                        continue
                    blob = recv_exact(conn, length)
                    opened = self._open_checked(blob)
                    if opened is None:
                        # 偶发失败 (时间偏差/重放) 可容忍; 连续失败说明
                        # 对端口令已不同 (如本机刚换口令), 断开释放槽位
                        bad += 1
                        if bad >= 8:
                            return
                        continue
                    bad = 0
                    _, body = opened
                    if len(body) < 1 or body[0] not in (
                            KIND_TEXT, KIND_IMAGE, KIND_TEXT_Z):
                        continue
                    data = body[1:]
                    if body[0] == KIND_TEXT_Z:
                        data = _safe_decompress(data)
                        if data is None:
                            continue
                    # 去重哈希对解压后的内容计算, 与发送端一致
                    h = hashlib.sha256(data).digest()
                    with self.lock:
                        if h == self.last_hash:
                            continue
                        self.last_hash = h
                    if body[0] == KIND_IMAGE:
                        # 在网络线程解码, 大图不卡界面线程
                        image = QImage.fromData(data, "PNG")
                        if image.isNull():
                            continue
                        self.bridge.remote_clip.emit(
                            {"type": "image", "data": image})
                        self.bridge.status.emit(f"收到图片 来自 {addr[0]}")
                    else:
                        self.bridge.remote_clip.emit(
                            {"type": "text", "data": data})
                        self.bridge.status.emit(f"收到文本 来自 {addr[0]}")
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
                fp = _image_fingerprint(obj)
                if self._img_cache is not None and self._img_cache[0] == fp:
                    data = self._img_cache[1]  # 同一张图重复触发, 免于再编码
                else:
                    buf = QBuffer()
                    buf.open(QIODevice.WriteOnly)
                    obj.save(buf, "PNG", PNG_QUALITY)
                    data = bytes(buf.data())
                    if not data:
                        continue
                    self._img_cache = (fp, data)
            else:
                data = obj.encode("utf-8")
            h = hashlib.sha256(data).digest()
            with self.lock:
                if h == self.last_hash:
                    continue  # 是我们自己刚设置的内容, 跳过
                self.last_hash = h
                # peers 按 node_id 记录, 节点重启后旧记录超时前
                # 同一 IP 会短暂出现两条, 用集合去重
                targets = {ip for ip, _, _ in self.peers.values()}
            if len(data) > MAX_PAYLOAD:
                self.bridge.status.emit(
                    f"内容过大 ({len(data) >> 20} MB), 超过 "
                    f"{MAX_PAYLOAD >> 20} MB 上限, 不同步 (对端会拒收)")
                continue
            if not targets:
                continue
            # 压缩放在哈希去重之后: last_hash 两端都对未压缩内容计算
            if kind == KIND_TEXT and len(data) >= COMPRESS_MIN:
                packed = zlib.compress(data, 6)
                if len(packed) < len(data):
                    kind, data = KIND_TEXT_Z, packed
            blob = self._seal(HEADER.pack(time.time(), NODE)
                              + bytes([kind]) + data)
            packet = MAGIC + struct.pack("!I", len(blob)) + blob
            name = "图片" if kind == KIND_IMAGE else "文本"
            self.bridge.status.emit(f"推送{name} 到 {len(targets)} 个节点")
            for ip in targets:
                self._enqueue_send(ip, packet)

    def _enqueue_send(self, ip: str, packet: bytes):
        # 入队须持锁, 与 _retire_dead_senders 的摘除互斥, 否则内容可能
        # 落进已投退出信号的孤儿队列而丢失 (put_nowait 不阻塞, 持锁无害)
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
        conn = None  # 长连接复用, 省去每条消息一次 TCP 握手的延迟
        while True:
            packet = q.get()
            if packet is None:
                if conn is not None:
                    conn.close()
                return  # 节点已下线, 退出线程 (见 _retire_dead_senders)
            fresh = conn is None or _sock_stale(conn)
            try:
                if fresh:
                    if conn is not None:
                        conn.close()
                    conn = socket.create_connection((ip, TRANSFER_PORT),
                                                    timeout=10)
                conn.sendall(packet)
                continue
            except OSError as e:
                if conn is not None:
                    conn.close()
                    conn = None
                if fresh:  # 全新连接都失败, 重试大概率无益
                    self.bridge.status.emit(f"发送到 {ip} 失败: {e}")
                    continue
            # 复用的旧连接在检测后才失效, 换全新连接重试一次
            try:
                conn = socket.create_connection((ip, TRANSFER_PORT),
                                                timeout=10)
                conn.sendall(packet)
            except OSError as e:
                if conn is not None:
                    conn.close()
                    conn = None
                self.bridge.status.emit(f"发送到 {ip} 失败: {e}")

    def _retire_dead_senders(self):
        """回收已不再是发送目标的节点的发送线程, 避免 IP 长期变动时累积。
        目标 = 已知节点 ∪ 手动节点 ∪ gossip 学到的节点。"""
        with self.lock:
            live = ({ip for ip, _, _ in self.peers.values()}
                    | self.manual_peers | set(self._gossip))
            stale = [(ip, self._senders.pop(ip))
                     for ip in list(self._senders) if ip not in live]
        for _, q in stale:
            # 该队列已从 _senders 移除, 不会再有新内容入队, 可安全清空并投退出信号
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass
            q.put_nowait(None)


def resource_path(*parts) -> str:
    """兼容 PyInstaller 打包 (资源解压到 sys._MEIPASS)。"""
    base = getattr(sys, "_MEIPASS",
                   os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


# 优先用带标准边距的图标 (窗口/托盘与 Dock 的 .icns 视觉一致), 缺失时回退原图
ICON_PATH = resource_path("assets", "icon_padded.png")
if not os.path.exists(ICON_PATH):
    ICON_PATH = resource_path("assets", "stellar_smart_share_clipboard.png")


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


def _version_key(v: str):
    nums = []
    for part in v.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits) if digits else 0)
    return tuple((nums + [0, 0, 0])[:3])


def _asset_suffix() -> str:
    if sys.platform == "darwin":
        return ".dmg"
    if sys.platform.startswith("win"):
        return ".exe"
    return ".deb"


class _DownloadCancelled(Exception):
    pass


class Updater(QObject):
    """检查 GitHub Releases 新版本并下载对应平台的安装包。"""
    update_available = Signal(str, str, str)  # version, notes, download_url
    up_to_date = Signal(bool)                 # manual: 是否弹窗提示
    progress = Signal(int, int)               # 已下载字节, 总字节 (未知为 0)
    cancelled = Signal()
    downloaded = Signal(str)                  # 安装包本地路径
    failed = Signal(str, bool)                # message, manual

    def __init__(self):
        super().__init__()
        self._cancel = False

    def cancel_download(self):
        self._cancel = True

    def check_async(self, manual: bool):
        threading.Thread(target=self._check, args=(manual,),
                         daemon=True).start()

    def _check(self, manual: bool):
        try:
            req = urllib.request.Request(
                UPDATE_API, headers={"User-Agent": "stellar-clipboard"})
            with urllib.request.urlopen(req, timeout=15,
                                        context=SSL_CONTEXT) as resp:
                info = json.load(resp)
            latest = info.get("tag_name", "").lstrip("vV")
            if not latest or _version_key(latest) <= _version_key(APP_VERSION):
                self.up_to_date.emit(manual)
                return
            suffix = _asset_suffix()
            url = next((a["browser_download_url"]
                        for a in info.get("assets", [])
                        if a["name"].endswith(suffix)), None)
            if not url:
                self.failed.emit(
                    f"新版本 v{latest} 暂无本平台 ({suffix}) 安装包", manual)
                return
            self.update_available.emit(
                latest, (info.get("body") or "")[:4000], url)
        except Exception as e:
            self.failed.emit(f"检查更新失败: {e}", manual)

    def download_async(self, url: str):
        threading.Thread(target=self._download, args=(url,),
                         daemon=True).start()

    def _download(self, url: str):
        self._cancel = False
        dest = os.path.join(tempfile.gettempdir(), url.rsplit("/", 1)[-1])
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "stellar-clipboard"})
            with urllib.request.urlopen(req, timeout=60,
                                        context=SSL_CONTEXT) as resp, \
                    open(dest, "wb") as f:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    if self._cancel:
                        raise _DownloadCancelled
                    f.write(chunk)
                    done += len(chunk)
                    self.progress.emit(done, total)
            self.downloaded.emit(dest)
        except _DownloadCancelled:
            try:
                os.remove(dest)
            except OSError:
                pass
            self.cancelled.emit()
        except Exception as e:
            self.failed.emit(f"下载更新失败: {e}", True)


# 独立于主进程的安装脚本: 等应用退出 -> 挂载 dmg -> 原子替换 .app -> 重启。
# 先复制到 TARGET.update 再交换, 避免复制中途失败留下残缺安装。
MAC_UPDATE_SCRIPT = """#!/bin/sh
PID="$1"; DMG="$2"; TARGET="$3"
exec >>"${TMPDIR:-/tmp}/stellar_update.log" 2>&1
while kill -0 "$PID" 2>/dev/null; do sleep 0.2; done
MNT=$(mktemp -d)
hdiutil attach -nobrowse -readonly -mountpoint "$MNT" "$DMG" || exit 1
APP=$(ls -d "$MNT"/*.app | head -1)
NEW="$TARGET.update"
rm -rf "$NEW"
if ditto "$APP" "$NEW"; then
    rm -rf "$TARGET" && mv "$NEW" "$TARGET"
fi
hdiutil detach "$MNT" -quiet
rm -f "$DMG"
open "$TARGET"
"""

# 等应用退出 -> pkexec (系统授权弹窗) 安装 deb -> 重启; 无 pkexec 时回退软件中心
LINUX_UPDATE_SCRIPT = """#!/bin/sh
PID="$1"; DEB="$2"; BIN="$3"
exec >>"${TMPDIR:-/tmp}/stellar_update.log" 2>&1
while kill -0 "$PID" 2>/dev/null; do sleep 0.2; done
if command -v pkexec >/dev/null 2>&1 && pkexec dpkg -i "$DEB"; then
    rm -f "$DEB"
    setsid "$BIN" >/dev/null 2>&1 &
else
    xdg-open "$DEB" || true
fi
"""


def _mac_bundle_path():
    """当前运行的 .app 包路径; 非打包运行时返回 None。"""
    if not getattr(sys, "frozen", False):
        return None
    p = os.path.abspath(sys.executable)
    while p != "/":
        if p.endswith(".app"):
            return p
        p = os.path.dirname(p)
    return None


def _spawn_update_script(template: str, package_path: str, target: str):
    script = os.path.join(tempfile.gettempdir(), "stellar_update.sh")
    with open(script, "w", encoding="utf-8") as f:
        f.write(template)
    os.chmod(script, 0o755)
    subprocess.Popen(["/bin/sh", script, str(os.getpid()),
                      package_path, target],
                     start_new_session=True)


class SecretDialog(QDialog):
    """打包运行 (无终端) 时的口令输入框。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Stellar 剪贴板同步")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("请输入共享口令 (所有机器必须一致):"))
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.edit)
        self.remember = QCheckBox(
            "在本机记住口令 (保存到系统钥匙串)" if keyring is not None
            else "在本机记住口令 (明文保存, 仅当前用户可读)")
        self.remember.setChecked(True)
        layout.addWidget(self.remember)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


ALIAS_FILE = os.path.expanduser("~/.stellar_clipboard_aliases.json")


def load_aliases() -> dict:
    try:
        with open(ALIAS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): str(v) for k, v in data.items()}
    except (OSError, ValueError):
        return {}


def save_aliases(aliases: dict):
    try:
        with open(ALIAS_FILE, "w", encoding="utf-8") as f:
            json.dump(aliases, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


class MainWindow(QWidget):
    """主窗口: 状态 / 在线节点 / 同步记录。关闭时隐藏到托盘, 不退出。"""

    def __init__(self, engine: SyncEngine, secret: str):
        super().__init__()
        self.engine = engine
        self.aliases = load_aliases()
        self._peers = []  # [(ip, hostname)]
        self.setWindowTitle(f"Stellar 剪贴板同步 v{APP_VERSION}")
        self.resize(420, 480)

        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.status_label = QLabel("● 运行中")
        self.status_label.setStyleSheet("color: #2E9E44; font-weight: bold;")
        header.addWidget(self.status_label)
        header.addStretch()
        header.addWidget(QLabel(f"本机节点 {NODE_ID[:8]}"))
        layout.addLayout(header)

        self.info_label = QLabel()
        self.info_label.setStyleSheet("color: gray;")
        self.set_secret_display(secret)
        layout.addWidget(self.info_label)

        self.pause_box = QCheckBox("暂停同步")
        self.pause_box.toggled.connect(self.on_pause_toggled)
        layout.addWidget(self.pause_box)

        self.peer_label = QLabel("在线节点 (0):")
        layout.addWidget(self.peer_label)
        self.peer_list = QListWidget()
        self.peer_list.setMaximumHeight(110)
        self.peer_list.setToolTip("双击节点可设置别名")
        self.peer_list.itemDoubleClicked.connect(self.on_peer_double_clicked)
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

    def set_secret_display(self, secret: str):
        masked = (secret[:2] + "••••••") if len(secret) > 2 else "••••••"
        self.info_label.setText(
            f"口令: {masked}    端口: UDP {DISCOVERY_PORT} / "
            f"TCP {TRANSFER_PORT}")

    def _peer_text(self, ip: str, host: str) -> str:
        name = self.aliases.get(host or ip) or host
        return f"{name} ({ip})" if name else ip

    def update_peers(self, items: list):
        """items: [(ip, hostname)]"""
        self._peers = list(items)
        self.peer_label.setText(f"在线节点 ({len(items)}):")
        self.peer_list.clear()
        self.peer_list.addItems(
            [self._peer_text(ip, host) for ip, host in self._peers])

    def on_peer_double_clicked(self, item):
        row = self.peer_list.row(item)
        if not 0 <= row < len(self._peers):
            return
        ip, host = self._peers[row]
        key = host or ip
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "设置别名", f"为 {host or ip} ({ip}) 设置别名 (留空清除):",
            text=self.aliases.get(key, ""))
        if not ok:
            return
        if text.strip():
            self.aliases[key] = text.strip()
        else:
            self.aliases.pop(key, None)
        save_aliases(self.aliases)
        self.update_peers(self._peers)

    def append_log(self, message: str):
        self.log_view.appendPlainText(
            f"[{time.strftime('%H:%M:%S')}] {message}")

    def closeEvent(self, event):
        event.ignore()
        self.hide()


class App:
    def __init__(self, secret, manual_peers=()):
        self.app = QApplication(sys.argv)
        icon = make_app_icon()
        self.app.setWindowIcon(icon)  # 主窗口随 QApplication 继承此图标
        self.app.setQuitOnLastWindowClosed(False)

        if not secret:  # 打包运行时没有终端, 用对话框要口令
            dlg = SecretDialog()
            if dlg.exec() != QDialog.Accepted or not dlg.edit.text():
                sys.exit(1)
            secret = dlg.edit.text()
            if dlg.remember.isChecked():
                try:
                    save_secret(secret)
                except OSError:
                    pass

        self.clipboard = self.app.clipboard()
        self._applying = False  # 正在把远端内容写入剪贴板, 抑制回环广播

        self.bridge = Bridge()
        self.engine = SyncEngine(secret, self.bridge, manual_peers)
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
        secret_action = QAction("修改口令", menu)
        secret_action.triggered.connect(self.on_change_secret)
        menu.addAction(secret_action)
        update_action = QAction("检查更新", menu)
        update_action.triggered.connect(
            lambda: self.updater.check_async(True))
        menu.addAction(update_action)
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.app.quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("Stellar 剪贴板同步 — 0 节点在线")
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

        self.clipboard.dataChanged.connect(self.on_local_change)
        self._install_sigint_handler()

        self.updater = Updater()
        self._progress = None
        self.updater.update_available.connect(self.on_update_available)
        self.updater.up_to_date.connect(self.on_up_to_date)
        self.updater.failed.connect(self.on_update_failed)
        self.updater.progress.connect(self.on_update_progress)
        self.updater.cancelled.connect(self.on_update_cancelled)
        self.updater.downloaded.connect(self.on_update_downloaded)
        QTimer.singleShot(5000, lambda: self.updater.check_async(False))

    def on_update_available(self, version: str, notes: str, url: str):
        box = QMessageBox(self.window)
        box.setWindowTitle("发现新版本")
        box.setText(f"发现新版本 v{version} (当前 v{APP_VERSION})。"
                    f"是否下载并安装?")
        if notes:
            box.setDetailedText(notes)
        yes = box.addButton("下载并安装", QMessageBox.AcceptRole)
        box.addButton("暂不", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is yes:
            self.window.append_log(f"正在下载 v{version} 安装包…")
            from PySide6.QtWidgets import QProgressDialog
            self._progress = QProgressDialog(
                "正在下载更新…", "取消", 0, 100, self.window)
            self._progress.setWindowTitle(f"下载 v{version}")
            self._progress.setMinimumDuration(0)
            self._progress.setValue(0)
            self._progress.canceled.connect(self.updater.cancel_download)
            self.updater.download_async(url)

    def on_update_progress(self, done: int, total: int):
        if self._progress is None:
            return
        if total > 0:
            self._progress.setMaximum(100)
            self._progress.setValue(min(99, done * 100 // total))
            self._progress.setLabelText(
                f"正在下载更新… {done / 1e6:.0f} / {total / 1e6:.0f} MB")
        else:
            self._progress.setMaximum(0)  # 总大小未知时显示忙碌动画

    def _close_progress(self):
        if self._progress is not None:
            self._progress.canceled.disconnect(self.updater.cancel_download)
            self._progress.close()
            self._progress = None

    def on_update_cancelled(self):
        self._close_progress()
        self.window.append_log("已取消下载")

    def on_up_to_date(self, manual: bool):
        self.window.append_log(f"已是最新版本 v{APP_VERSION}")
        if manual:
            QMessageBox.information(self.window, "检查更新",
                                    f"当前已是最新版本 (v{APP_VERSION})")

    def on_update_failed(self, message: str, manual: bool):
        self._close_progress()
        self.window.append_log(message)
        if manual:
            QMessageBox.warning(self.window, "检查更新", message)

    def on_update_downloaded(self, path: str):
        self._close_progress()
        if sys.platform == "darwin":
            target = _mac_bundle_path()
            if target and os.access(os.path.dirname(target), os.W_OK):
                self.window.append_log("下载完成, 退出后自动替换安装并重启…")
                _spawn_update_script(MAC_UPDATE_SCRIPT, path, target)
                QTimer.singleShot(500, self.app.quit)
                return
            # 源码运行或安装目录不可写: 退回打开 dmg 手动安装
            self.window.append_log("下载完成, 请把应用拖入 Applications 完成安装")
            subprocess.Popen(["open", path])
        elif sys.platform.startswith("win"):
            self.window.append_log("下载完成, 静默安装后将自动重启…")
            subprocess.Popen([path, "/SILENT", "/FORCECLOSEAPPLICATIONS"])
        else:
            if getattr(sys, "frozen", False):
                self.window.append_log("下载完成, 退出后请在系统弹窗中授权安装, "
                                       "完成后自动重启…")
                _spawn_update_script(LINUX_UPDATE_SCRIPT, path, sys.executable)
                QTimer.singleShot(500, self.app.quit)
                return
            self.window.append_log("下载完成, 请手动安装")
            subprocess.Popen(["xdg-open", path])
        QTimer.singleShot(800, self.app.quit)

    def _install_sigint_handler(self):
        """终端里连按两次 Ctrl+C (2 秒内) 退出程序。
        打包成 GUI 应用时没有终端, 直接跳过——既省去无用的信号处理,
        也避免那个每 200ms 空转的定时器。"""
        if not (sys.stdin is not None and sys.stdin.isatty()):
            return
        self._sigint_at = 0.0

        def handler(signum, frame):
            now = time.monotonic()
            if now - self._sigint_at <= 2.0:
                print("\n退出", flush=True)
                self.app.quit()
            else:
                self._sigint_at = now
                print("\n再按一次 Ctrl+C 退出 (2 秒内)", flush=True)

        signal.signal(signal.SIGINT, handler)
        # Qt 事件循环阻塞在 C++ 侧时 Python 信号处理器不会被执行,
        # 用一个空转定时器周期性回到解释器, 让信号得以处理
        self._sigint_timer = QTimer()
        self._sigint_timer.timeout.connect(lambda: None)
        self._sigint_timer.start(200)

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
                self.clipboard.setImage(msg["data"])  # 已在网络线程解码
        finally:
            # dataChanged 可能同步触发, 也可能经事件队列; 用零延时定时器
            # 在这些事件处理完之后再解除抑制
            QTimer.singleShot(0, self._clear_applying)

    def _clear_applying(self):
        self._applying = False

    def on_peers_changed(self, items: list):
        self.window.update_peers(items)
        self.peer_action.setText(f"节点: {len(items)} 在线")
        self.tray.setToolTip(f"Stellar 剪贴板同步 — {len(items)} 节点在线")

    def on_change_secret(self):
        dlg = SecretDialog()
        dlg.setWindowTitle("修改口令")
        self.show_window()
        if dlg.exec() != QDialog.Accepted or not dlg.edit.text():
            return
        secret = dlg.edit.text()
        self.engine.rekey(secret)
        self.window.set_secret_display(secret)
        if dlg.remember.isChecked():
            try:
                save_secret(secret)
            except OSError:
                pass
        else:  # 不记住: 清掉本机保存的旧口令
            forget_secret()

    def run(self) -> int:
        try:
            self.engine.start()
        except RuntimeError as e:
            QMessageBox.critical(None, "无法启动", str(e))
            return 1
        self.window.show()
        self.window.append_log(f"已启动 v{APP_VERSION}, 本机节点 {NODE_ID[:8]}, "
                               f"图形后端 {self.app.platformName()}")
        if self.engine.manual_peers:
            self.window.append_log(
                "手动节点: " + ", ".join(sorted(self.engine.manual_peers)))
        if self.app.platformName() == "wayland":
            self.window.append_log(
                "警告: 正运行在 Wayland 后端, 本机复制的内容可能无法同步出去; "
                "请安装 xcb 相关库或用 QT_QPA_PLATFORM=xcb 启动")
        self.window.append_log("等待发现同网段节点…")
        return self.app.exec()


SECRET_FILE = os.path.expanduser("~/.stellar_clipboard_secret")
KEYRING_SERVICE = "stellar-smart-share-clipboard"
KEYRING_USER = "shared-secret"


def load_saved_secret():
    if keyring is not None:
        try:
            secret = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
            if secret:
                return secret
        except Exception:
            pass
    try:
        with open(SECRET_FILE, "r", encoding="utf-8") as f:
            secret = f.read().strip() or None
    except OSError:
        return None
    if secret and keyring is not None:
        try:
            save_secret(secret)  # 把旧版明文文件迁移进钥匙串并删除
        except OSError:
            pass
    return secret


def save_secret(secret: str):
    if keyring is not None:
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, secret)
        except Exception:
            pass  # 钥匙串不可用, 回退明文文件
        else:
            try:
                os.remove(SECRET_FILE)  # 清理旧版遗留的明文文件
            except OSError:
                pass
            return
    fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(secret)


def forget_secret():
    """删除本机保存的口令 (钥匙串条目和旧明文文件都清)。"""
    if keyring is not None:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
        except Exception:
            pass
    try:
        os.remove(SECRET_FILE)
    except OSError:
        pass


def resolve_secret(cli_secret):
    """--secret > 环境变量 SSSC_SECRET > 本机保存的口令 > 终端交互输入。
    都没有时返回 None, 由 App 弹 GUI 对话框输入。"""
    secret = (cli_secret or os.environ.get("SSSC_SECRET")
              or load_saved_secret())
    if not secret and sys.stdin is not None and sys.stdin.isatty():
        secret = getpass.getpass("请输入共享口令 (输入不回显): ")
    return secret


def _fix_linux_clipboard_backend():
    """Wayland 不允许后台应用监听剪贴板变化 (Ubuntu 22.04+ 默认 Wayland),
    表现为本机复制的内容不会同步出去 (接收正常)。强制优先用 XWayland
    (xcb 后端), 其剪贴板由合成器桥接, 后台监听可用; xcb 加载失败时
    回退 wayland, 保证程序至少能启动。"""
    if (sys.platform.startswith("linux")
            and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            and "QT_QPA_PLATFORM" not in os.environ):
        os.environ["QT_QPA_PLATFORM"] = "xcb;wayland"


def main():
    _fix_linux_clipboard_backend()
    parser = argparse.ArgumentParser(description="局域网剪贴板同步 (Qt 版)")
    parser.add_argument("--secret",
                        help="共享口令, 所有机器必须一致, 请使用足够复杂的口令。"
                             "为避免泄露到 shell 历史和进程列表, 推荐改用"
                             "环境变量 SSSC_SECRET 或留空后交互输入")
    parser.add_argument("--peer", action="append", default=[], metavar="IP",
                        help="手动指定对端 IP (可多次)。用于广播不可达的网络: "
                             "路由器过滤广播、跨网段、VPN (如 Tailscale)。"
                             "也可用环境变量 SSSC_PEERS=ip1,ip2")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {APP_VERSION}")
    args = parser.parse_args()
    peers = args.peer + os.environ.get("SSSC_PEERS", "").split(",")
    sys.exit(App(resolve_secret(args.secret), peers).run())


if __name__ == "__main__":
    main()
