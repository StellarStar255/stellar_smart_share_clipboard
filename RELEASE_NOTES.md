# Stellar Clipboard v2.0.7

## 中文

内部清理(无功能变化)。

- **回收下线节点的发送线程**:此前每见过一个新 IP 就常驻一个发送
  线程,节点下线后不清理。现在节点超时下线时会一并退出其发送线程,
  避免 IP 长期变动(如 DHCP 频繁换址)时线程缓慢累积
- **打包 GUI 版去掉多余的空转定时器**:用于终端响应 Ctrl+C 的
  200ms 定时器现在仅在从终端启动时启用;打包成 App(无终端)时
  不再每秒空转 5 次

从 v2.0.6 升级为全自动流程。

---

## English

Internal cleanup (no functional change).

- **Retire send threads for offline peers**: previously every peer IP
  ever seen kept a resident send thread that was never cleaned up. When
  a peer times out its send thread now exits too, preventing slow thread
  accumulation on networks where IPs churn (e.g. DHCP).
- **Drop the idle timer in the packaged GUI**: the 200ms timer that lets
  Ctrl+C work from a terminal is now armed only when launched from a
  terminal; the packaged app (no terminal) no longer wakes 5×/second.

Upgrading from v2.0.6 uses the fully automatic flow.
