# Stellar Clipboard v2.0.4

## 中文

节点发现可靠性大幅增强。

- **广播 + 单播保活**:部分路由器会抑制/丢弃 UDP 广播转发,导致节点
  时隐时现甚至完全互相发现不了。现在除受限广播外,还会向本子网定向
  广播地址、以及所有已知节点补发单播心跳——任一方向哪怕偶然通过一次,
  双方即转入稳定的单播互相保活,不再抖动掉线
- **新增 `--peer IP` 手动节点**(可多次,或环境变量 `SSSC_PEERS=ip1,ip2`):
  用于广播完全不可达的网络——跨网段、VPN(如 Tailscale)。只需一侧
  配置,双方即可互相发现;手动节点自动加入 TCP 连接白名单
- 启动日志显示已配置的手动节点

从 v2.0.3 升级本版本即可体验全自动升级流程(macOS 零操作原地替换,
Ubuntu 系统授权弹窗,Windows 静默安装)。

---

## English

Much more reliable peer discovery.

- **Broadcast + unicast keep-alive**: some routers suppress or drop
  forwarded UDP broadcasts, making peers flap or never discover each
  other. Announcements now also go to the subnet-directed broadcast
  address and, crucially, as unicast to every known peer — once either
  direction gets through even once, both sides switch to stable unicast
  keep-alive and stop flapping
- **New `--peer IP` manual peers** (repeatable, or env
  `SSSC_PEERS=ip1,ip2`): for networks where broadcast can't reach at
  all — different subnets, VPNs such as Tailscale. Configuring one side
  is enough for mutual discovery; manual peers are also allowed through
  the TCP connection allowlist
- The startup log lists configured manual peers

Upgrading from v2.0.3 exercises the new fully automatic update flow
(zero-touch in-place replace on macOS, system auth dialog on Ubuntu,
silent install on Windows).
