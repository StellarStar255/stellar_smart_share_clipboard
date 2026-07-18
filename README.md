# Stellar Smart Share Clipboard

在同一局域网内的 Mac 和 Windows (以及 Linux) 之间同步剪贴板。
无需服务器, 各机器自动互相发现, 点对点直传。

## 使用方法

两台电脑上都安装依赖并运行同一个脚本:

```bash
pip install -r requirements.txt
python clipboard_share_qt.py
```

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

- `--secret 口令` — 配对口令, 所有机器必须一致 (默认 `stellar-clipboard`)。
  消息带 HMAC-SHA256 校验, 口令不同的实例互不干扰。

## 原理

- UDP 广播 (端口 48765): 周期性宣告自身, 自动发现节点, 10 秒无心跳即视为下线
- TCP (端口 48766): 剪贴板内容点对点推送, 长度前缀 + HMAC 签名帧
- 内容哈希去重, 防止两台机器互相触发的同步回环

## 注意

- 防火墙需放行 UDP 48765 与 TCP 48766 (首次运行时 macOS/Windows 会弹窗询问, 选择允许)
- 两台机器需在同一广播域 (同一路由器/网段); 部分公司网络或访客 Wi-Fi 会屏蔽广播
- 内容以明文传输 (仅 HMAC 防篡改), 请只在可信局域网内使用

## Repo

