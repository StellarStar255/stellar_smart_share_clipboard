# Stellar Smart Share Clipboard

在同一局域网内的 Mac 和 Windows (以及 Linux) 之间同步剪贴板。
无需服务器, 各机器自动互相发现, 点对点直传。

## 安装包 (推荐)

到 [Releases](https://github.com/StellarStar255/stellar_smart_share_clipboard/releases/latest)
下载对应平台的安装包: macOS `.dmg` / Windows `-setup.exe` / Ubuntu `.deb`,
一键安装; 之后可在托盘菜单"检查更新"里一键升级。

## 从源码运行

两台电脑上都安装依赖并运行同一个脚本:

```bash
pip install -r requirements.txt
python clipboard_share_qt.py --secret 你的口令

# 推荐: 用环境变量传口令, 避免进入 shell 历史和进程列表
SSSC_SECRET=你的口令 python clipboard_share_qt.py     # macOS / Linux
$env:SSSC_SECRET="你的口令"; python clipboard_share_qt.py  # Windows PowerShell

# 两者都不提供时, 启动会提示交互输入 (不回显)
```

所有机器必须使用相同的口令。剪贴板可能包含密码等敏感内容,
请使用足够复杂的口令 (弱口令可被离线字典攻击破解)。

支持文本 + 图片同步, 主窗口显示在线节点与同步记录, 关闭窗口后
最小化到系统托盘继续后台运行。

### Windows 上安装 PySide6 失败?

- `DLL load failed while importing QtCore`: 先安装
  [VC++ 运行库](https://aka.ms/vs/17/release/vc_redist.x64.exe);
  conda 环境下建议 `conda install -c conda-forge pyside6`
- `No such file or directory: ...qml\...obj` (商店版 Python 长路径问题):
  以管理员运行 PowerShell 执行下面命令后重启电脑, 再
  `pip uninstall -y PySide6 PySide6_Essentials PySide6_Addons shiboken6`
  清理残留并重新 `pip install PySide6`:

  ```powershell
  New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
  ```

启动后会自动发现同网段的其他实例, 之后在任一台电脑复制的内容都会
出现在其他电脑的剪贴板中。

## 选项

- `--secret 口令` — 配对口令, 所有机器必须一致, 口令不同的实例互不干扰。
  取值顺序: `--secret` > 环境变量 `SSSC_SECRET` > 交互输入。
- `--peer IP` — 手动指定对端 IP (可多次), 或环境变量 `SSSC_PEERS=ip1,ip2`。
  用于 UDP 广播不可达的网络: 路由器广播抑制、跨网段、VPN (如 Tailscale)。
  只需一侧配置即可, 双方会通过单播互相发现。

## 原理

- UDP 发现 (端口 48765): 周期性广播宣告自身, 并对已知/手动节点补发单播
  (应对路由器广播抑制); 10 秒无心跳即视为下线
- TCP (端口 48766): 剪贴板内容点对点推送, 长度前缀 + 加密二进制帧;
  只接受已通过发现认证的节点 IP 的连接, 且限制并发连接数;
  每个节点一个按序发送队列, 保证先复制的内容先送达
- 所有消息用 ChaCha20-Poly1305 加密认证, 密钥由口令经 scrypt 派生;
  消息携带时间戳和一次性 nonce, 防止重放攻击 (要求各机器系统时间大致同步,
  偏差需小于 30 秒)
- 内容哈希去重 + 应用远端内容时抑制回环广播

## 注意

- 防火墙需放行 UDP 48765 与 TCP 48766 (首次运行时 macOS/Windows 会弹窗询问, 选择允许)
- 两台机器需在同一广播域 (同一路由器/网段); 部分公司网络或访客 Wi-Fi 会屏蔽广播
- 内容已加密传输, 但请仍然为每组机器使用独立且复杂的口令;
  各机器系统时间偏差超过 30 秒会导致消息被当作重放丢弃

## Repo

