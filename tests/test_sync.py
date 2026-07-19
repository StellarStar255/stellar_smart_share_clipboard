"""同步引擎测试: 长连接收发 / 重连 / 压缩 / 口令存储 / 线程回收。

运行: python -m unittest discover tests
需要本机装有 PySide6; 测试全部走 127.0.0.1 随机端口, 不碰真实网络。
"""
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
import zlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

APP = QApplication.instance() or QApplication([])

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage

import clipboard_share_qt as m

# 收发引擎在同一进程共享模块级 NODE, 发消息时用伪造节点 ID 绕过自发过滤
FAKE_NODE = b"\x01" * 16


class FakeSig:
    def __init__(self):
        self.msgs = []

    def emit(self, *args):
        self.msgs.append(args)


class FakeBridge:
    def __init__(self):
        self.remote_clip = FakeSig()
        self.peers_changed = FakeSig()
        self.status = FakeSig()


def make_packet(engine, kind, data, node=FAKE_NODE):
    blob = engine._seal(m.HEADER.pack(time.time(), node)
                        + bytes([kind]) + data)
    return m.MAGIC + struct.pack("!I", len(blob)) + blob


def start_receiver(engine):
    """给引擎绑随机端口并启动接收循环, 把 TRANSFER_PORT 指过去。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(m.MAX_CONNECTIONS)
    engine._srv_sock = srv
    engine.peers["test-peer"] = ("127.0.0.1", time.time(), "tester")
    m.TRANSFER_PORT = srv.getsockname()[1]
    threading.Thread(target=engine._server_loop, daemon=True).start()


def wait_for(cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.03)
    return cond()


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def solid_image(color, size=16):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(QColor(color))
    return img


def png_bytes(img):
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


class TestHelpers(unittest.TestCase):
    def test_image_fingerprint(self):
        a = m._image_fingerprint(solid_image("red"))
        b = m._image_fingerprint(solid_image("red"))
        c = m._image_fingerprint(solid_image("blue"))
        self.assertEqual(a, b)      # 相同内容不同对象 -> 相同指纹
        self.assertNotEqual(a, c)
        self.assertIsInstance(m._image_fingerprint(QImage()), bytes)

    def test_sock_stale(self):
        a, b = socket.socketpair()
        self.assertFalse(m._sock_stale(a))
        b.close()
        self.assertTrue(m._sock_stale(a))   # 对端关闭 -> 可读 -> 判失效
        a.close()
        self.assertTrue(m._sock_stale(a))   # 已关的 fd 不崩溃

    def test_discard_exact_keeps_stream_aligned(self):
        a, b = socket.socketpair()
        a.sendall(b"x" * 1000 + b"TAIL")
        m.discard_exact(b, 1000)
        self.assertEqual(m.recv_exact(b, 4), b"TAIL")
        a.close()
        b.close()

    def test_sanitize_filename(self):
        self.assertEqual(m._sanitize_filename("a.txt"), "a.txt")
        self.assertEqual(m._sanitize_filename("../../etc/passwd"), "passwd")
        self.assertEqual(m._sanitize_filename("..\\..\\x.exe"), "x.exe")
        self.assertEqual(m._sanitize_filename(".."), "unnamed")
        self.assertEqual(m._sanitize_filename(""), "unnamed")

    def test_unpack_files_roundtrip_and_garbage(self):
        payload = b"".join(
            struct.pack("!H", len(n)) + n + struct.pack("!I", len(c)) + c
            for n, c in [("甲.txt".encode(), b"AAA"), (b"b.bin", b"")])
        self.assertEqual(m._unpack_files(payload),
                         [("甲.txt", b"AAA"), ("b.bin", b"")])
        self.assertIsNone(m._unpack_files(payload[:-1]))   # 截断
        self.assertIsNone(m._unpack_files(b""))            # 空
        self.assertIsNone(m._unpack_files(b"\xff\xff junk"))

    def test_save_incoming_files_dedup_names(self):
        old = m.FILES_DIR
        with tempfile.TemporaryDirectory() as tmp:
            m.FILES_DIR = tmp
            try:
                paths = m._save_incoming_files(
                    [("a.txt", b"1"), ("a.txt", b"2"), ("../a.txt", b"3")])
            finally:
                m.FILES_DIR = old
            self.assertEqual([os.path.basename(p) for p in paths],
                             ["a.txt", "a-1.txt", "a-2.txt"])
            self.assertEqual([read_bytes(p) for p in paths],
                             [b"1", b"2", b"3"])

    def test_safe_decompress(self):
        self.assertEqual(m._safe_decompress(zlib.compress(b"abc")), b"abc")
        self.assertIsNone(m._safe_decompress(b"not-zlib"))
        bomb = zlib.compress(b"\x00" * (m.MAX_PAYLOAD + 1024), 9)
        self.assertIsNone(m._safe_decompress(bomb))          # 解压后超限
        cut = zlib.compress(b"hello world" * 100)[:-5]
        self.assertIsNone(m._safe_decompress(cut))           # 截断


class TestReceiver(unittest.TestCase):
    """接收端: 长连接多消息 / 暂停丢弃 / 图片解码 / 压缩 / 坏消息处理。"""

    def setUp(self):
        self.recv = m.SyncEngine("secret", FakeBridge())
        start_receiver(self.recv)
        self.push = m.SyncEngine("secret", FakeBridge())

    def got(self):
        return self.recv.bridge.remote_clip.msgs

    def send(self, kind, data, engine=None):
        (engine or self.push)._enqueue_send(
            "127.0.0.1", make_packet(engine or self.push, kind, data))

    def test_multiple_messages_one_connection(self):
        self.send(m.KIND_TEXT, b"hello")
        self.send(m.KIND_TEXT, b"world")
        self.assertTrue(wait_for(lambda: len(self.got()) == 2))
        self.assertEqual([a[0]["data"] for a in self.got()],
                         [b"hello", b"world"])
        self.assertEqual(len(self.push._senders), 1)  # 单发送线程/长连接

    def test_pause_discards_then_same_conn_resumes(self):
        self.recv.paused = True
        self.send(m.KIND_TEXT, b"skipped")
        time.sleep(0.4)
        self.assertEqual(len(self.got()), 0)
        self.recv.paused = False
        self.send(m.KIND_TEXT, b"resumed")
        self.assertTrue(wait_for(lambda: len(self.got()) == 1))
        self.assertEqual(self.got()[0][0]["data"], b"resumed")

    def test_image_decoded_off_gui_thread(self):
        self.send(m.KIND_IMAGE, png_bytes(solid_image("green")))
        self.assertTrue(wait_for(lambda: len(self.got()) == 1))
        msg = self.got()[0][0]
        self.assertEqual(msg["type"], "image")
        self.assertIsInstance(msg["data"], QImage)  # 网络线程已解码
        self.assertFalse(msg["data"].isNull())

    def test_files_saved_to_disk_and_emitted(self):
        old = m.FILES_DIR
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(setattr, m, "FILES_DIR", old)
        m.FILES_DIR = self._tmp.name
        payload = b"".join(
            struct.pack("!H", len(n)) + n + struct.pack("!I", len(c)) + c
            for n, c in [(b"doc.pdf", b"PDF-DATA"), (b"note.txt", b"hi")])
        self.send(m.KIND_FILES, payload)
        self.assertTrue(wait_for(lambda: len(self.got()) == 1))
        msg = self.got()[0][0]
        self.assertEqual(msg["type"], "files")
        self.assertEqual([os.path.basename(p) for p in msg["data"]],
                         ["doc.pdf", "note.txt"])
        self.assertEqual(read_bytes(msg["data"][0]), b"PDF-DATA")

    def test_malformed_files_payload_dropped(self):
        self.send(m.KIND_FILES, b"\xff\xff not a valid pack")
        self.send(m.KIND_TEXT, b"still-alive")
        self.assertTrue(wait_for(lambda: len(self.got()) == 1))
        self.assertEqual(self.got()[0][0]["data"], b"still-alive")

    def test_compressed_text_roundtrip(self):
        text = ("你好 " * 2000).encode()
        self.send(m.KIND_TEXT_Z, zlib.compress(text, 6))
        self.assertTrue(wait_for(lambda: len(self.got()) == 1))
        self.assertEqual(self.got()[0][0]["data"], text)

    def test_bad_payloads_dropped_but_conn_survives(self):
        self.send(m.KIND_TEXT_Z, b"garbage")
        bomb = zlib.compress(b"\x00" * (m.MAX_PAYLOAD + 1024), 9)
        self.send(m.KIND_TEXT_Z, bomb)
        self.send(m.KIND_TEXT, b"still-alive")
        self.assertTrue(wait_for(lambda: len(self.got()) == 1))
        self.assertEqual(self.got()[0][0]["data"], b"still-alive")

    def test_wrong_secret_closes_after_8_failures(self):
        stranger = m.SyncEngine("wrong-secret", FakeBridge())
        conn = socket.create_connection(("127.0.0.1", m.TRANSFER_PORT),
                                        timeout=3)
        conn.settimeout(3)
        for _ in range(8):
            conn.sendall(make_packet(stranger, m.KIND_TEXT, b"x"))
        self.assertEqual(conn.recv(1), b"")  # 连续解密失败 -> 对端断开
        conn.close()
        # 正常口令的消息仍然可以走新连接送达
        self.send(m.KIND_TEXT, b"good")
        self.assertTrue(wait_for(lambda: len(self.got()) == 1))


class TestSenderPersistence(unittest.TestCase):
    def test_reconnect_when_peer_closes_each_message(self):
        """对端 (如旧版本) 每收一条就断开时, 发送端应检测失效并重连。"""
        received = []

        def one_shot_server(srv):
            while True:
                conn, _ = srv.accept()
                with conn:
                    header = m.recv_exact(conn, len(m.MAGIC) + 4)
                    (length,) = struct.unpack("!I", header[len(m.MAGIC):])
                    received.append(m.recv_exact(conn, length))

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        m.TRANSFER_PORT = srv.getsockname()[1]
        threading.Thread(target=one_shot_server, args=(srv,),
                         daemon=True).start()

        eng = m.SyncEngine("s", FakeBridge())
        for payload in (b"AAAAA", b"BBBBB", b"CCCCC"):
            eng._enqueue_send(
                "127.0.0.1", m.MAGIC + struct.pack("!I", 5) + payload)
            time.sleep(0.3)  # 留出对端收完关闭的间隔, 触发 stale 检测
        self.assertTrue(wait_for(lambda: len(received) == 3))
        self.assertEqual(received, [b"AAAAA", b"BBBBB", b"CCCCC"])
        failures = [a for a in eng.bridge.status.msgs if "失败" in a[0]]
        self.assertEqual(failures, [])


class TestDispatch(unittest.TestCase):
    """发送端编码/压缩/去重: 用间谍服务器解密检查线上格式。"""

    def setUp(self):
        self.wire = []
        self.sender = m.SyncEngine("s", FakeBridge())

        def spy(srv):
            while True:
                conn, _ = srv.accept()
                with conn:
                    conn.settimeout(10)
                    try:
                        while True:
                            header = m.recv_exact(conn, len(m.MAGIC) + 4)
                            (length,) = struct.unpack(
                                "!I", header[len(m.MAGIC):])
                            blob = m.recv_exact(conn, length)
                            nonce, ct = (blob[:m.NONCE_LEN],
                                         blob[m.NONCE_LEN:])
                            plain = self.sender.cipher.decrypt(
                                nonce, ct, m.MAGIC)
                            self.wire.append(plain[m.HEADER.size:])
                    except (ConnectionError, socket.timeout, OSError):
                        pass

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        m.TRANSFER_PORT = srv.getsockname()[1]
        threading.Thread(target=spy, args=(srv,), daemon=True).start()
        self.sender.peers["n"] = ("127.0.0.1", time.time(), "t")
        threading.Thread(target=self.sender._dispatch_loop,
                         daemon=True).start()

    def test_big_text_compressed_small_text_not_dup_deduped(self):
        big = "import os\n" * 1000  # 10KB, 高度可压缩
        self.sender.submit(m.KIND_TEXT, big)
        self.sender.submit(m.KIND_TEXT, big)      # 重复, 应被去重
        self.sender.submit(m.KIND_TEXT, "hello")  # 小文本, 保持旧格式
        self.assertTrue(wait_for(lambda: len(self.wire) == 2))
        time.sleep(0.3)
        self.assertEqual(len(self.wire), 2)       # 确认重复没被发出
        self.assertEqual(self.wire[0][0], m.KIND_TEXT_Z)
        self.assertLess(len(self.wire[0]), len(big) // 5)
        self.assertEqual(zlib.decompress(self.wire[0][1:]), big.encode())
        self.assertEqual(self.wire[1][0], m.KIND_TEXT)
        self.assertEqual(self.wire[1][1:], b"hello")

    def test_files_packed_and_oversize_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1 = os.path.join(tmp, "a.txt")
            p2 = os.path.join(tmp, "b.bin")
            with open(p1, "w") as f:
                f.write("hello")
            with open(p2, "wb") as f:
                f.write(b"\x00\x01\x02")
            self.sender.submit(m.KIND_FILES, [p1, p2])
            self.assertTrue(wait_for(lambda: len(self.wire) == 1))
            self.assertEqual(self.wire[0][0], m.KIND_FILES)
            self.assertEqual(m._unpack_files(self.wire[0][1:]),
                             [("a.txt", b"hello"), ("b.bin", b"\x00\x01\x02")])
            # 大小预检: 超限文件不读入内存、不发送, 只提示
            old_max = m.MAX_PAYLOAD
            m.MAX_PAYLOAD = 4
            try:
                self.sender.submit(m.KIND_FILES, [p1])
                self.assertTrue(wait_for(lambda: any(
                    "文件过大" in a[0]
                    for a in self.sender.bridge.status.msgs)))
            finally:
                m.MAX_PAYLOAD = old_max
            self.assertEqual(len(self.wire), 1)

    def test_image_encode_cache_and_dedup(self):
        img = solid_image("teal", 64)
        self.sender.submit(m.KIND_IMAGE, img)
        self.sender.submit(m.KIND_IMAGE, solid_image("teal", 64))  # 同内容
        self.assertTrue(wait_for(lambda: len(self.wire) == 1))
        time.sleep(0.3)
        self.assertEqual(len(self.wire), 1)
        self.assertEqual(self.wire[0][0], m.KIND_IMAGE)
        decoded = QImage.fromData(self.wire[0][1:], "PNG")
        self.assertFalse(decoded.isNull())


class TestRekey(unittest.TestCase):
    def test_rekey_retires_sender_threads(self):
        eng = m.SyncEngine("old", FakeBridge())
        eng.peers["n"] = ("127.0.0.1", time.time(), "t")
        m.TRANSFER_PORT = 1  # 连不上也无妨, 只验证线程回收
        eng._enqueue_send("127.0.0.1", b"whatever")
        self.assertEqual(len(eng._senders), 1)
        eng.rekey("new")
        self.assertEqual(eng._senders, {})        # 发送线程已投退出信号
        self.assertEqual(eng.peers, {})
        self.assertEqual(eng._nonces, {})

    def test_rekey_keeps_manual_peers(self):
        eng = m.SyncEngine("old", FakeBridge(), manual_peers=["10.0.0.9"])
        m.TRANSFER_PORT = 1
        eng._enqueue_send("10.0.0.9", b"whatever")
        eng.rekey("new")
        self.assertIn("10.0.0.9", eng._senders)   # 手动节点仍是发送目标


class TestSecretStorage(unittest.TestCase):
    """keyring 存取与旧明文文件迁移。无可用后端 (如无桌面 CI) 时跳过。"""

    def setUp(self):
        if m.keyring is None:
            self.skipTest("keyring 未安装")
        self._service = m.KEYRING_SERVICE
        self._file = m.SECRET_FILE
        m.KEYRING_SERVICE = "stellar-smart-share-clipboard-test"
        self._tmp = tempfile.TemporaryDirectory()
        m.SECRET_FILE = os.path.join(self._tmp.name, "secret")
        try:
            m.keyring.set_password(m.KEYRING_SERVICE, m.KEYRING_USER, "probe")
            ok = m.keyring.get_password(
                m.KEYRING_SERVICE, m.KEYRING_USER) == "probe"
        except Exception:
            ok = False
        if not ok:
            self.tearDown()
            self.skipTest("keyring 无可用后端")
        m.forget_secret()

    def tearDown(self):
        try:
            m.forget_secret()
        finally:
            m.KEYRING_SERVICE = self._service
            m.SECRET_FILE = self._file
            self._tmp.cleanup()

    def test_save_load_forget(self):
        self.assertIsNone(m.load_saved_secret())
        m.save_secret("p@ss-测试")
        self.assertEqual(m.load_saved_secret(), "p@ss-测试")
        self.assertFalse(os.path.exists(m.SECRET_FILE))  # 不再写明文
        m.forget_secret()
        self.assertIsNone(m.load_saved_secret())

    def test_migrates_legacy_plaintext_file(self):
        with open(m.SECRET_FILE, "w", encoding="utf-8") as f:
            f.write("legacy-secret\n")
        self.assertEqual(m.load_saved_secret(), "legacy-secret")
        self.assertFalse(os.path.exists(m.SECRET_FILE))  # 迁移后删除
        self.assertEqual(
            m.keyring.get_password(m.KEYRING_SERVICE, m.KEYRING_USER),
            "legacy-secret")


if __name__ == "__main__":
    unittest.main()
