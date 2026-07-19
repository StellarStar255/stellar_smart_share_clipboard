# Stellar Clipboard v2.1.0

## 中文

性能与安全改进。

- **同步更快**:与每个节点保持 TCP 长连接,免去每条消息一次握手的
  延迟;收到的图片在网络线程解码,大截图不再卡住界面;PNG 编码提速
  约 40% 且体积相当;同一张图重复触发不再重复编码
- **大文本压缩**:≥4KB 的文本先 zlib 压缩再加密传输,日志、代码等
  高冗余内容体积可缩小数倍
- **口令入系统钥匙串**:记住的口令改存 macOS 钥匙串 / Windows
  凭据管理器 / Linux Secret Service,不再明文落盘;旧版保存的明文
  文件首次启动时自动迁移并删除
- **更健壮**:修改口令后正确回收旧节点的发送线程与连接;连续解密
  失败的连接会被断开;暂停同步时收到的内容不再解密,只消费字节流
- 新增自动化测试套件(17 个用例,覆盖协议收发、重连、压缩、口令
  存储迁移)

注意:压缩文本使用了新的消息类型,2.0.7 及更早版本会忽略这类消息。
新旧版本混跑期间,≥4KB 的大文本无法同步到旧版(小文本和图片不受
影响),请把所有机器都升级到 2.1.0。

从 v2.0.x 升级为全自动流程。

---

## English

Performance and security improvements.

- **Faster sync**: a persistent TCP connection per peer removes the
  per-message handshake latency; incoming images decode on the network
  thread so large screenshots no longer freeze the UI; PNG encoding is
  ~40% faster at equal size; duplicate clipboard events for the same
  image no longer re-encode it
- **Text compression**: text ≥ 4KB is zlib-compressed before
  encryption; highly redundant content (logs, code) shrinks severalfold
- **Secret in the system keychain**: the remembered passphrase now
  lives in macOS Keychain / Windows Credential Manager / Linux Secret
  Service instead of a plaintext file; the legacy file is migrated and
  deleted automatically on first launch
- **More robust**: changing the passphrase now retires stale sender
  threads and connections; connections failing decryption repeatedly
  are dropped; while paused, incoming payloads are drained without
  being decrypted
- New automated test suite (17 cases covering transfer, reconnect,
  compression, and secret migration)

Note: compressed text uses a new message kind that 2.0.7 and earlier
silently ignore. While versions are mixed, text ≥ 4KB will not reach
old peers (small text and images are unaffected) — please upgrade all
machines to 2.1.0.

Upgrading from v2.0.x uses the fully automatic flow.
