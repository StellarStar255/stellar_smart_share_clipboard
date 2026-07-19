#!/usr/bin/env bash
# 把 PyInstaller onedir 产物打成 .deb。用法: build_deb.sh <version>
set -euo pipefail

VERSION="${1:?usage: build_deb.sh <version>}"
APP=stellar-clipboard
ROOT=build/debroot

rm -rf "$ROOT"
mkdir -p "$ROOT/DEBIAN" "$ROOT/opt/$APP" "$ROOT/usr/bin" \
         "$ROOT/usr/share/applications" "$ROOT/usr/share/pixmaps"

cp -r "dist/$APP/." "$ROOT/opt/$APP/"
ln -s "/opt/$APP/$APP" "$ROOT/usr/bin/$APP"
cp assets/stellar_smart_share_clipboard.png "$ROOT/usr/share/pixmaps/$APP.png"

cat > "$ROOT/usr/share/applications/$APP.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Stellar Clipboard
Comment=Encrypted LAN clipboard sync / 局域网剪贴板同步
Exec=/opt/$APP/$APP
Icon=$APP
Terminal=false
Categories=Utility;
EOF

cat > "$ROOT/DEBIAN/control" <<EOF
Package: $APP
Version: $VERSION
Section: utils
Priority: optional
Architecture: amd64
Maintainer: StellarStar255 <goosehuangmatt@gmail.com>
Description: Encrypted LAN clipboard sync (text + images)
 Synchronizes the clipboard between machines on the same LAN
 with authenticated encryption (ChaCha20-Poly1305), automatic
 peer discovery and replay protection.
EOF

dpkg-deb --build --root-owner-group "$ROOT" "dist/${APP}_${VERSION}_amd64.deb"
echo "built dist/${APP}_${VERSION}_amd64.deb"
