# Stellar Clipboard v2.2.2

## 中文

修复:重新复制同一个文件/内容永远不会再次同步。

- 防回环的内容去重原来没有时效:某个文件同步过一次之后,再怎么
  重新复制都会被当成"自己刚设置的内容"静默丢弃——表现为"这个文件
  永远同步不过去,换个文件就行"
- 现在去重只在 5 秒内生效(仍能拦截 macOS 一次复制多次触发和
  刚收到内容的回流竞态);超过 5 秒重新复制同一内容,视为用户
  主动重发,正常同步
- 收发两端同时修复;测试套件扩充到 27 个用例

建议与 v2.2.1(识别并恢复被 Synergy/NoMachine 覆盖的文件剪贴板)
一起使用,所有机器都升到 2.2.2。

---

## English

Fix: re-copying the same file/content never synced again.

- The anti-loop content dedup had no expiry: once a file had been
  synced, every later re-copy was silently dropped as "our own echo" —
  observed as "this one file never syncs, but any other file does"
- Dedup now only applies within a 5-second window (still catching
  macOS's multiple dataChanged firings per copy and just-received
  content racing back); re-copying the same content after that is
  treated as a deliberate resend and syncs normally
- Fixed on both the sending and receiving side; test suite grown to
  27 cases
- Best combined with v2.2.1 (detect and restore file clipboards
  clobbered by Synergy/NoMachine) — upgrade all machines to 2.2.2.
