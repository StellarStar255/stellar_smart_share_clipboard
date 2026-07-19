# Stellar Clipboard v2.0.1

## 中文

修复版本。

- **修复安装包内"检查更新"报 SSL 证书错误**
  (`CERTIFICATE_VERIFY_FAILED`):打包后的应用找不到系统 CA 证书库,
  现已捆绑 certifi 证书。从本版本起应用内一键升级可正常工作。

> 已安装 v2.0.0 的用户:该版本的更新检查因此 bug 无法使用,
> 请手动下载本版本安装一次,之后即可应用内升级。

安装方法与注意事项同 v2.0.0(macOS 未签名应用需右键 → 打开;
所有机器使用相同口令;防火墙放行 UDP 48765 / TCP 48766)。

---

## English

Bugfix release.

- **Fix SSL certificate error in the packaged app's update check**
  (`CERTIFICATE_VERIFY_FAILED`): the bundled Python could not find the
  system CA store; certifi certificates are now bundled. In-app one-click
  updates work from this version onward.

> If you installed v2.0.0: its update check is broken by this bug —
> please download and install this version manually once; future
> upgrades can then be done in-app.

Installation and notes are the same as v2.0.0 (unsigned macOS app needs
right-click → Open; use the same passphrase on all machines; allow
UDP 48765 / TCP 48766 through the firewall).
