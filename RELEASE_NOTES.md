# Stellar Clipboard v2.0.0

## 中文

在同一局域网内的 Mac / Windows / Ubuntu 之间同步剪贴板(文本 + 图片),
无需服务器,自动发现节点,点对点直传。首个提供安装包的正式版本。

### 新特性

- **端到端加密**:所有流量使用 ChaCha20-Poly1305 认证加密,密钥由口令经
  scrypt 派生;携带时间戳与一次性 nonce,防重放、防篡改
- **连接加固**:TCP 只接受已通过发现认证的节点,并限制并发连接数
- **应用内一键升级**:托盘菜单"检查更新",自动下载对应平台安装包并启动安装
- **更顺手**:首次启动图形界面输入口令(可记住)、复制大图不再卡界面、
  连续复制保证按序送达、终端里连按两次 Ctrl+C 退出

### 安装

| 平台 | 文件 | 说明 |
|---|---|---|
| macOS | `StellarClipboard-*-macos.dmg` | 打开后把应用拖入 Applications。首次运行如提示"无法验证开发者",请右键 → 打开;或执行 `xattr -dr com.apple.quarantine /Applications/StellarClipboard.app` |
| Windows | `StellarClipboard-*-windows-setup.exe` | 双击安装(免管理员权限)。如出现 SmartScreen 提示,点"更多信息 → 仍要运行" |
| Ubuntu | `stellar-clipboard_*_amd64.deb` | 双击用软件中心安装,或 `sudo apt install ./stellar-clipboard_*_amd64.deb` |

### 使用

1. 每台电脑安装并启动,输入**相同的口令**(请用足够复杂的口令,弱口令可被离线破解)
2. 同一局域网内自动互相发现,复制的内容会出现在其他机器的剪贴板
3. 防火墙需放行 UDP 48765 / TCP 48766;各机器系统时间偏差需小于 30 秒

> 注意:v2 加密协议与旧的明文脚本版本不兼容,所有机器请一起升级。

---

## English

Sync your clipboard (text + images) between Mac / Windows / Ubuntu machines
on the same LAN. Serverless, automatic peer discovery, peer-to-peer transfer.
This is the first release shipping installers.

### Highlights

- **End-to-end encryption**: all traffic is authenticated-encrypted with
  ChaCha20-Poly1305, key derived from your passphrase via scrypt; messages
  carry a timestamp and one-time nonce for replay and tamper protection
- **Hardened networking**: TCP only accepts connections from discovered,
  authenticated peers, with a concurrent-connection cap
- **One-click in-app updates**: "Check for updates" in the tray menu
  downloads the right installer for your platform and launches it
- **Quality of life**: GUI passphrase prompt on first launch (with remember
  option), large-image copies no longer block the UI, rapid copies are
  delivered in order, double Ctrl+C quits when run from a terminal

### Install

| Platform | File | Notes |
|---|---|---|
| macOS | `StellarClipboard-*-macos.dmg` | Drag the app to Applications. If Gatekeeper complains about an unidentified developer, right-click → Open, or run `xattr -dr com.apple.quarantine /Applications/StellarClipboard.app` |
| Windows | `StellarClipboard-*-windows-setup.exe` | Double-click to install (no admin required). If SmartScreen appears, choose "More info → Run anyway" |
| Ubuntu | `stellar-clipboard_*_amd64.deb` | Install via Software Center, or `sudo apt install ./stellar-clipboard_*_amd64.deb` |

### Usage

1. Install and launch on every machine with the **same passphrase**
   (use a strong one — weak passphrases can be brute-forced offline)
2. Machines on the same LAN discover each other automatically; anything you
   copy appears in the other machines' clipboards
3. Allow UDP 48765 / TCP 48766 through the firewall; system clocks must be
   within 30 seconds of each other

> Note: the v2 encrypted protocol is incompatible with the old plaintext
> script version — upgrade all machines together.
