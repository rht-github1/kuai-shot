#!/usr/bin/env bash
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
DEST="${DEST:-$PREFIX/share/kuai-shot}"
BIN="${BIN:-$PREFIX/bin/kuai-shot}"
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/kuai-shot.desktop"
export DEST

stop_old() {
  if command -v pkill >/dev/null 2>&1; then
    pkill -f '/share/kuai-shot/kuai-shot' >/dev/null 2>&1 || true
    pkill -f '/share/feishu-shot/feishu-shot' >/dev/null 2>&1 || true
    pkill -f "$DEST/kuai-shot" >/dev/null 2>&1 || true
    pkill -f "$HOME/.local/share/kuai-shot/kuai-shot" >/dev/null 2>&1 || true
    pkill -f "$HOME/.local/share/feishu-shot/feishu-shot" >/dev/null 2>&1 || true
  fi
  rm -f "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/feishu-shot.sock"
  rm -f "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/kuai-shot.sock"
}

stop_old
rm -f "$PREFIX/bin/feishu-shot"
rm -rf "$PREFIX/share/feishu-shot"
rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/feishu-shot.desktop"

mkdir -p "$(dirname "$BIN")" "$DEST"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete --exclude '.git' --exclude '__pycache__' "$SRC/" "$DEST/"
else
  rm -rf "$DEST"
  mkdir -p "$DEST"
  cp -a "$SRC/." "$DEST/"
fi
chmod +x "$DEST/kuai-shot" "$DEST/install.sh"

cat > "$BIN" <<EOF
#!/usr/bin/env bash
export KUAI_SHOT_HOME="$DEST"
export GIO_LAUNCHED_DESKTOP_FILE="$DESKTOP"
exec python3 "$DEST/kuai-shot" "\$@"
EOF
chmod +x "$BIN"

python3 - <<'PY'
from pathlib import Path
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QApplication
import os

root = Path(os.environ.get("DEST") or Path.home() / ".local/share/kuai-shot")
app = QApplication([])
pix = QPixmap(128, 128)
pix.fill(Qt.transparent)
p = QPainter(pix)
p.setRenderHint(QPainter.Antialiasing, True)
p.setBrush(QColor("#3370FF"))
p.setPen(Qt.NoPen)
p.drawRoundedRect(8, 8, 112, 112, 28, 28)
p.setPen(QPen(QColor("#FFFFFF"), 8))
p.setBrush(Qt.NoBrush)
p.drawRoundedRect(32, 38, 64, 48, 8, 8)
p.setBrush(QColor("#FFFFFF"))
p.setPen(Qt.NoPen)
p.drawEllipse(70, 30, 18, 18)
p.end()
root.mkdir(parents=True, exist_ok=True)
pix.save(str(root / "icon.png"))
PY

python3 - <<PY
import os, sys
sys.path.insert(0, "$DEST")
os.environ["KUAI_SHOT_HOME"] = "$DEST"
from kuai_shot.app import install_user_integration
install_user_integration("$BIN")
print("已写入开机自启动和快捷键 Ctrl+Alt+A")
PY

stop_old
nohup "$BIN" daemon >/dev/null 2>&1 &
echo "已安装到 $DEST"
echo "启动命令: $BIN"
echo "快捷键: Ctrl+Alt+A"
echo "托盘图标可右键截图或退出"
