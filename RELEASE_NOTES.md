# Stellar Clipboard v2.0.5

## 中文

新特性 + 发现增强。

- **节点互相介绍 (gossip)**:心跳现在携带已知节点名单,节点会互相
  转介。这样即使两台机器之间广播不可达(如两台设备只和第三台广播通),
  它们也能通过共同的节点得知彼此并直接单播互连——修复"两台 Ubuntu
  只看得到 Mac、看不到对方"的问题
- **显示主机名**:在线节点列表显示"主机名 (IP)"而不再只有 IP
- **自定义别名**:双击任一节点可设置便于记忆的别名(保存在本机)
- **修改口令**:托盘菜单新增"修改口令",无需重启即可更换,更换后
  自动与使用新口令的节点重新配对

从 v2.0.4 升级即为全自动流程。

---

## English

New features + smarter discovery.

- **Peer gossip**: heartbeats now carry the list of known peers, so nodes
  introduce each other. Even when two machines can't reach each other by
  broadcast (e.g. both only broadcast-reach a third), they learn about
  each other through a common peer and connect directly via unicast —
  fixes "two Ubuntu boxes see the Mac but not each other"
- **Hostnames**: the peer list shows "hostname (IP)" instead of just IP
- **Custom aliases**: double-click any peer to set a memorable alias
  (stored locally)
- **Change passphrase**: a new "Change passphrase" tray item lets you
  switch passphrases without restarting; the app re-pairs with peers on
  the new passphrase automatically

Upgrading from v2.0.4 uses the fully automatic flow.
