# Stellar Clipboard v2.0.3

## 中文

升级体验大幅改进(从本版本升级到以后的版本时生效)。

- **下载进度条**:应用内更新显示真实下载进度(MB),可随时取消
- **macOS 全自动升级**:不再需要手动拖拽——下载完成后应用自动退出,
  后台自动挂载 dmg、原子替换 `/Applications` 中的应用并重启新版本;
  且应用内更新不再触发 Gatekeeper 的"无法验证开发者"提示
- **Ubuntu 自动安装**:不再依赖软件中心——弹出系统授权窗口,
  输入密码后自动 `dpkg -i` 安装并重启程序
- **Windows 静默升级**:安装器以 /SILENT 运行,装完自动重启程序

> 提示:本次从 v2.0.2 升级到 v2.0.3 仍走旧流程(macOS 需手动拖拽一次),
> 上述全自动体验从 v2.0.3 升级到后续版本时开始生效。

---

## English

Major upgrade-experience improvements (effective when upgrading FROM this
version to future releases).

- **Download progress**: in-app updates now show real download progress
  (MB) with a cancel button
- **Fully automatic upgrades on macOS**: no more manual dragging — after
  download the app quits, a background helper mounts the dmg, atomically
  replaces the app in `/Applications` and relaunches the new version;
  in-app updates also no longer trigger Gatekeeper's unidentified
  developer prompt
- **Automatic install on Ubuntu**: no more Software Center dependency —
  a system authentication dialog appears, then the package is installed
  via `dpkg -i` and the app restarts
- **Silent upgrade on Windows**: the installer runs with /SILENT and
  relaunches the app when done

> Note: upgrading from v2.0.2 to v2.0.3 still uses the old flow (one
> manual drag on macOS); the fully automatic experience kicks in when
> upgrading from v2.0.3 onward.
