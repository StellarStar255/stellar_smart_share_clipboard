# Stellar Clipboard v2.0.2

## 中文

修复版本。

- **修复 Ubuntu (Wayland) 上本机复制不同步的问题**:Ubuntu 22.04+ 默认的
  Wayland 会话禁止后台应用监听剪贴板,导致 Ubuntu → 其他机器方向失效
  (接收方向不受影响)。现在程序会自动改走 XWayland (xcb),后台监听恢复
  正常;X11 会话及 macOS / Windows 行为不变。
- 启动日志显示实际使用的图形后端;若仍运行在 Wayland 后端会给出
  明确警告与解决指引。

已安装 v2.0.1 的用户可直接在托盘菜单"检查更新"一键升级。
(v2.0.0 用户请手动下载安装,该版本的更新检查有 SSL 缺陷。)

---

## English

Bugfix release.

- **Fix local copies not syncing out on Ubuntu (Wayland)**: the default
  Wayland session on Ubuntu 22.04+ forbids background apps from monitoring
  the clipboard, breaking the Ubuntu → others direction (receiving was
  unaffected). The app now runs via XWayland (xcb) automatically, restoring
  background clipboard monitoring; X11 sessions, macOS and Windows are
  unchanged.
- The startup log now shows the graphics backend in use, with a clear
  warning and instructions if the app still ends up on the Wayland backend.

Users on v2.0.1 can upgrade in one click via "Check for updates" in the
tray menu. (v2.0.0 users: please download manually — that version's update
check is broken by an SSL bug.)
