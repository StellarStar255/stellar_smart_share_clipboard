# Stellar Clipboard v2.2.1

## 中文

修复:与 Synergy / NoMachine 等键鼠共享软件共存时,文件同步失效。

- 这类软件自带的剪贴板同步不支持文件:切换屏幕时会把"复制的文件"
  降级成路径文本,覆盖本程序刚写入对端剪贴板的文件,导致粘贴无反应
- 现在两端都会记住最近一批同步过的文件;一旦剪贴板被覆盖成对应的
  路径文本,立即识别并把文件恢复回剪贴板(日志中会提示),同时不把
  这段路径文本误当作复制的文字广播出去
- 识别规则:纯文本、每一行都能对应到批内文件(完整路径 / file://
  URL / 文件名),距最近一次文件同步不超过 10 分钟;正常复制的文字
  不受影响
- 测试套件扩充到 25 个用例

无需改动 Synergy / NoMachine 的设置即可共存;如果不需要它们的剪贴板
共享,关掉后干扰更少。

---

## English

Fix: file sync no longer breaks when Synergy / NoMachine style
keyboard-and-mouse sharing tools are running.

- Their built-in clipboard sync does not support files: on screen
  switch they downgrade "copied files" to plain path text, overwriting
  the files this app just placed on the other machine's clipboard, so
  pasting did nothing
- Both ends now remember the most recent batch of synced files; when
  the clipboard gets clobbered with matching path text, it is detected
  and the files are restored to the clipboard immediately (with a log
  message), and the path text is not mistakenly broadcast as copied text
- Detection rule: plain text only, every line must map to a file in the
  batch (full path / file:// URL / file name), within 10 minutes of the
  last file sync; normal text copies are unaffected
- Test suite grown to 25 cases

No Synergy / NoMachine configuration changes are required; disabling
their clipboard sharing still reduces interference if you don't need it.
