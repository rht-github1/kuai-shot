#!/usr/bin/env bash
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
DEST="${DEST:-$PREFIX/share/kuai-shot}"
BIN="${BIN:-$PREFIX/bin/kuai-shot}"
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/kuai-shot.desktop"
export DEST

DEPS=(
  python3
  python3-gi
  python3-gi-cairo
  python3-cairo
  python3-pyqt5
  qtwayland5
  gir1.2-gtk-3.0
  gir1.2-gdkpixbuf-2.0
  gir1.2-glib-2.0
  gir1.2-atspi-2.0
  gir1.2-gstreamer-1.0
  gir1.2-gst-plugins-base-1.0
  gstreamer1.0-plugins-base
  gstreamer1.0-plugins-good
  gstreamer1.0-pipewire
  xdg-desktop-portal
  xdg-desktop-portal-gnome
  xdg-user-dirs
  rsync
  python3-pip
)

pkg_installed() {
  dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'install ok installed'
}

_in_china() {
  local tz lang
  tz="$(readlink -f /etc/localtime 2>/dev/null || true)"
  lang="${LANG:-}${LC_ALL:-}${LANGUAGE:-}"
  [[ "$tz" == *Shanghai* || "$tz" == *Urumqi* || "$tz" == *Chongqing* ]] && return 0
  [[ "$lang" == *zh_CN* || "$lang" == *zh-CN* ]] && return 0
  return 1
}

_sources_already_cn() {
  local files=()
  [[ -f /etc/apt/sources.list ]] && files+=(/etc/apt/sources.list)
  if [[ -d /etc/apt/sources.list.d ]]; then
    shopt -s nullglob
    files+=(/etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources)
    shopt -u nullglob
  fi
  ((${#files[@]} == 0)) && return 1
  grep -Eiq 'mirrors\.(ustc\.edu\.cn|tuna\.tsinghua\.edu\.cn|aliyun\.com|163\.com|tencent\.com|cloud\.tencent\.com)|repo\.huaweicloud\.com|mirrors\.bfsu\.edu\.cn|mirror\.nju\.edu\.cn' "${files[@]}" 2>/dev/null
}

_write_mirror_list() {
  local base="$1" dest="$2" suite="${VERSION_CODENAME:-}"
  local arch repo security
  [[ -n "$suite" ]] || return 1
  arch="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
  if [[ "${ID:-}" == debian ]]; then
    {
      echo "deb ${base}/debian ${suite} main contrib non-free non-free-firmware"
      echo "deb ${base}/debian ${suite}-updates main contrib non-free non-free-firmware"
      echo "deb ${base}/debian-security ${suite}-security main contrib non-free non-free-firmware"
    } >"$dest"
    return 0
  fi
  if [[ "$arch" == amd64 || "$arch" == i386 ]]; then
    repo=ubuntu
  else
    repo=ubuntu-ports
  fi
  security="${base}/${repo}"
  {
    echo "deb ${base}/${repo} ${suite} main restricted universe multiverse"
    echo "deb ${base}/${repo} ${suite}-updates main restricted universe multiverse"
    echo "deb ${base}/${repo} ${suite}-backports main restricted universe multiverse"
    echo "deb ${security} ${suite}-security main restricted universe multiverse"
  } >"$dest"
}

_apt_run() {
  local extra=()
  if [[ -n "${KUAI_APT_SOURCES:-}" ]]; then
    extra=(
      -o "Dir::Etc::sourcelist=${KUAI_APT_SOURCES}"
      -o Dir::Etc::sourceparts=/dev/null
    )
  fi
  "${APT_CMD[@]}" "${extra[@]}" "$@"
}

_pip_host() {
  python3 -c 'from urllib.parse import urlparse; print(urlparse("'"$1"'").hostname or "")'
}

_pip_index_ok() {
  python3 - "$1" <<'PY'
import sys
from urllib.request import urlopen
url = sys.argv[1].rstrip("/") + "/pip/"
try:
    urlopen(url, timeout=3)
except Exception:
    sys.exit(1)
PY
}

setup_pip_mirror() {
  KUAI_PIP_INDEX=""
  local choice="${KUAI_SHOT_PIP_INDEX:-auto}"
  case "$choice" in
    0|off|official) return 0 ;;
  esac

  local indexes=()
  if [[ "$choice" != auto && "$choice" != 1 && "$choice" != cn ]]; then
    indexes=("${choice%/}")
  else
    if [[ "$choice" == auto ]] && ! _in_china; then
      return 0
    fi
    indexes=(
      https://pypi.tuna.tsinghua.edu.cn/simple
      https://mirrors.aliyun.com/pypi/simple
      https://mirrors.ustc.edu.cn/pypi/simple
      https://repo.huaweicloud.com/repository/pypi/simple
    )
  fi

  local idx
  for idx in "${indexes[@]}"; do
    [[ -n "$idx" ]] || continue
    if _pip_index_ok "$idx"; then
      KUAI_PIP_INDEX="$idx"
      export PIP_INDEX_URL="$idx"
      export PIP_TRUSTED_HOST="$(_pip_host "$idx")"
      echo "本次 pip 使用镜像：${idx}"
      break
    fi
    echo "pip 镜像不可用：${idx}" >&2
  done

  local conf="${XDG_CONFIG_HOME:-$HOME/.config}/pip/pip.conf"
  if [[ -n "${KUAI_PIP_INDEX:-}" && ! -f "$conf" && "${KUAI_SHOT_PIP_WRITE_CONF:-auto}" != "0" ]]; then
    if [[ "${KUAI_SHOT_PIP_WRITE_CONF:-auto}" == "1" ]] || { [[ "${KUAI_SHOT_PIP_WRITE_CONF:-auto}" == auto ]] && _in_china && [[ ! -f "$HOME/.pip/pip.conf" ]]; }; then
      mkdir -p "$(dirname "$conf")"
      cat >"$conf" <<EOF
[global]
index-url = ${KUAI_PIP_INDEX}
trusted-host = ${PIP_TRUSTED_HOST}
EOF
      echo "已写入 ${conf}（原先没有 pip 配置）"
    fi
  fi
}

pip_install() {
  local extra=()
  if [[ -n "${KUAI_PIP_INDEX:-}" ]]; then
    extra=(-i "$KUAI_PIP_INDEX" --trusted-host "${PIP_TRUSTED_HOST}")
  fi
  python3 -m pip install --user --upgrade "${extra[@]}" "$@"
}

pip_fallback() {
  echo "apt 模块不完整，尝试用 pip 补 PyQt5 / cairo"
  if ! python3 -m pip --version >/dev/null 2>&1; then
    echo "没有 pip，无法回退" >&2
    return 1
  fi
  pip_install PyQt5 pycairo
}

_mirror_bases() {
  local choice="${KUAI_SHOT_APT_MIRROR:-auto}"
  case "$choice" in
    0|off|official) return 1 ;;
    auto)
      if _sources_already_cn; then
        echo "系统 apt 已是国内镜像，沿用现有源" >&2
        return 1
      fi
      _in_china || return 1
      ;;
    1|cn) ;;
    *)
      printf '%s\n' "${choice%/}"
      return 0
      ;;
  esac
  printf '%s\n' \
    https://mirrors.ustc.edu.cn \
    https://mirrors.tuna.tsinghua.edu.cn \
    https://mirrors.aliyun.com \
    https://repo.huaweicloud.com
}

ensure_deps() {
  if [[ "${KUAI_SHOT_SKIP_DEPS:-}" == "1" ]]; then
    echo "已跳过依赖安装（KUAI_SHOT_SKIP_DEPS=1）"
    return 0
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "需要 python3" >&2
    exit 1
  fi
  if [[ ! -f /etc/os-release ]]; then
    echo "无法识别系统，请先手动安装依赖。" >&2
    exit 1
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}:${ID_LIKE:-}" in
    ubuntu:*|debian:*|*:debian*|*:ubuntu*) ;;
    *)
      echo "当前系统不是 Ubuntu/Debian，请自行安装：" >&2
      printf '  %s\n' "${DEPS[@]}" >&2
      exit 1
      ;;
  esac

  local missing=()
  local pkg
  for pkg in "${DEPS[@]}"; do
    if ! pkg_installed "$pkg"; then
      missing+=("$pkg")
    fi
  done
  if ((${#missing[@]} == 0)); then
    echo "依赖已就绪"
    return 0
  fi

  echo "缺少依赖：${missing[*]}"
  APT_CMD=(apt-get)
  if [[ "$(id -u)" -ne 0 ]]; then
    if command -v sudo >/dev/null 2>&1; then
      APT_CMD=(sudo apt-get)
    else
      echo "需要 root 或 sudo 才能安装：${missing[*]}" >&2
      exit 1
    fi
  fi
  export DEBIAN_FRONTEND=noninteractive
  KUAI_APT_SOURCES=""
  local mirror_dir=""
  local used_mirror=""
  local bases=()
  mapfile -t bases < <(_mirror_bases || true)
  if ((${#bases[@]} > 0)); then
    mirror_dir="$(mktemp -d)"
    KUAI_APT_SOURCES="$mirror_dir/sources.list"
    local base
    for base in "${bases[@]}"; do
      [[ -n "$base" ]] || continue
      _write_mirror_list "$base" "$KUAI_APT_SOURCES" || continue
      echo "尝试国内镜像：${base}"
      if _apt_run update -y; then
        used_mirror="$base"
        echo "已选用镜像：${base}"
        break
      fi
      echo "镜像不可用：${base}" >&2
    done
    if [[ -z "$used_mirror" ]]; then
      echo "国内镜像都不可用，回退系统源" >&2
      KUAI_APT_SOURCES=""
      _apt_run update -y
    fi
  else
    _apt_run update -y
  fi
  _apt_run install -y "${missing[@]}"
  [[ -n "$mirror_dir" ]] && rm -rf "$mirror_dir"
  echo "依赖已安装"
}

verify_python() {
  python3 - <<'PY'
import sys
errors = []
try:
    from PyQt5.QtWidgets import QApplication
except Exception as exc:
    errors.append(f"PyQt5: {exc}")
try:
    import cairo
except Exception as exc:
    errors.append(f"cairo: {exc}")
try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gst", "1.0")
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi, Gdk, Gst, Gtk
except Exception as exc:
    errors.append(f"GObject: {exc}")
if errors:
    print("Python 模块检查失败：", file=sys.stderr)
    print("\n".join(errors), file=sys.stderr)
    sys.exit(1)
print("Python 模块检查通过")
PY
}

ensure_python() {
  setup_pip_mirror
  if verify_python; then
    return 0
  fi
  pip_fallback
  verify_python
}

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

ensure_deps
ensure_python

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

if [[ ":$PATH:" != *":$PREFIX/bin:"* ]]; then
  echo "提示：当前 PATH 不含 $PREFIX/bin，可执行  export PATH=\"$PREFIX/bin:\$PATH\"  或重新登录"
fi

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
print("已写入开机自启动、截图权限和快捷键 Ctrl+Alt+A")
PY

stop_old
nohup "$BIN" daemon >/dev/null 2>&1 &
echo "已安装到 $DEST"
echo "启动命令: $BIN"
echo "快捷键: Ctrl+Alt+A"
echo "托盘图标可右键截图或退出"
