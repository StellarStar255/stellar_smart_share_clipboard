# Stellar Clipboard v2.2.0

## 中文

新增文件同步。

- **复制文件,直接粘贴**:在一台电脑复制文件(⌘C / Ctrl+C),到另
  一台直接粘贴(⌘V / Ctrl+V),粘出来的就是真正的文件;支持一次
  复制多个文件
- 单次总大小上限 64MB;暂不支持文件夹(会在日志中提示跳过),
  文件夹请先压缩成 zip 再复制
- 发送前按文件大小预检,超限的大文件不会被读进内存;接收的文件
  在网络线程落盘,不卡界面
- 收到的文件写入临时目录的独立子目录(同名文件自动加序号),
  文件名经过清洗,防止路径穿越
- 测试套件扩充到 23 个用例,覆盖文件打包/解包/落盘/超限拒发

注意:文件同步使用了新的消息类型,2.1.0 及更早版本会忽略这类消息。
新旧版本混跑期间,文件无法同步到旧版(文本和图片不受影响),请把
所有机器都升级到 2.2.0。

从 v2.1.x 升级为全自动流程。

---

## English

File sync.

- **Copy a file, paste it elsewhere**: copy files on one machine
  (⌘C / Ctrl+C) and paste real files on another (⌘V / Ctrl+V);
  multiple files per copy are supported
- Up to 64MB total per transfer; folders are not supported yet (a log
  message notes the skip) — zip them first
- File sizes are pre-checked before reading, so oversized files are
  never loaded into memory; received files are written to disk on the
  network thread without blocking the UI
- Incoming files land in a fresh temp subdirectory per batch (duplicate
  names get a numeric suffix); filenames are sanitized against path
  traversal
- Test suite grown to 23 cases, covering file packing, unpacking,
  saving, and oversize rejection

Note: file sync uses a new message kind that 2.1.0 and earlier silently
ignore. While versions are mixed, files will not reach old peers (text
and images are unaffected) — please upgrade all machines to 2.2.0.

Upgrading from v2.1.x uses the fully automatic flow.
