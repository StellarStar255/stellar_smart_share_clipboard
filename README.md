# Stellar Smart Share Clipboard

在同一局域网内的 Mac 和 Windows (以及 Linux) 之间同步剪贴板。
无需服务器, 各机器自动互相发现, 点对点直传。

## 使用方法

两台电脑上都安装依赖并运行同一个脚本:

```bash
pip install -r requirements.txt
python clipboard_share_qt.py --secret 你的口令
```

所有机器必须使用相同的 `--secret`。剪贴板可能包含密码等敏感内容,
请使用足够复杂的口令。

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

- `--secret 口令` — 配对口令 (必填), 所有机器必须一致。
  口令不同的实例互不干扰。

## 原理

- UDP 广播 (端口 48765): 周期性宣告自身, 自动发现节点, 10 秒无心跳即视为下线
- TCP (端口 48766): 剪贴板内容点对点推送, 长度前缀 + 加密二进制帧
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

