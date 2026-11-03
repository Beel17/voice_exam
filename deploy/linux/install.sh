#!/usr/bin/env bash
# Install (or upgrade) the voice exam engine on an x86-64 Linux server, as a systemd service.
#
#   sudo ./deploy/linux/install.sh                          # downloads the models (~2 GB)
#   sudo ./deploy/linux/install.sh --models /path/models    # or copies a models folder
#
# Run it from the unpacked package (tools/make_linux_package.py), which has the exam app
# already built. The program goes in /opt/voice-exam; everything that changes in
# /var/lib/voice-exam (configuration, device token, exam store, sessions); the service runs
# as user voiceexam and logs to the journal. Running it again upgrades the program and keeps
# the data and configuration. Needs internet once (Python packages, and the models unless
# copied) and uv (https://docs.astral.sh/uv/).
set -euo pipefail

PREFIX=/opt/voice-exam
DATA=/var/lib/voice-exam
USER_NAME=voiceexam
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
MODELS_FROM=""

while [ $# -gt 0 ]; do
  case "$1" in
    --models) MODELS_FROM="$(cd "$2" && pwd)"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\n== %s\n' "$*"; }
fail() { echo "error: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "run it with sudo"
[ "$(uname -m)" = x86_64 ] || fail "x86-64 Linux only so far"
[ -f "$HERE/src/voice_agent/exam/index.html" ] \
  || fail "the exam app is not built here: use the package made by tools/make_linux_package.py"
command -v systemctl >/dev/null || fail "systemd is needed"
UV="$(command -v uv || true)"
for candidate in /root/.local/bin/uv "${SUDO_USER:+/home/$SUDO_USER/.local/bin/uv}"; do
  if [ -z "$UV" ] && [ -n "$candidate" ] && [ -x "$candidate" ]; then UV="$candidate"; fi
done
[ -n "$UV" ] || fail "uv is needed: curl -LsSf https://astral.sh/uv/install.sh | sh (then run this again)"
if ! grep -qw avx2 /proc/cpuinfo; then
  echo "warning: this CPU has no AVX2, so the service runs on rules only (no language model)."
  echo "On a VM, give it the host's CPU features (CPU type 'host')."
fi

say "system library for the model server (OpenMP)"
if ! ldconfig -p | grep -q 'libgomp\.so\.1'; then
  command -v apt-get >/dev/null || fail "install the GNU OpenMP library (libgomp), then run this again"
  apt-get update -qq && apt-get install -y -qq libgomp1
fi

say "service user $USER_NAME"
id -u "$USER_NAME" >/dev/null 2>&1 \
  || useradd --system --home-dir "$DATA" --no-create-home --shell /usr/sbin/nologin "$USER_NAME"

say "program in $PREFIX"
systemctl stop voice-exam 2>/dev/null || true
mkdir -p "$PREFIX"
for part in src tools config pyproject.toml uv.lock README.md; do
  rm -rf "${PREFIX:?}/$part"
  cp -a "$HERE/$part" "$PREFIX/"
done
install -m 0755 "$HERE/deploy/linux/voice-exam" /usr/local/bin/voice-exam

say "Python environment"
cd "$PREFIX"
# A Python the service's user can run: the system's 3.12, or one uv keeps under the program.
UV_PYTHON_INSTALL_DIR="$PREFIX/python" "$UV" sync --frozen --no-dev --python 3.12 --quiet

say "models"
mkdir -p "$PREFIX/models"
if [ -n "$MODELS_FROM" ]; then
  # Everything but Windows' own programs; the Linux model server is fetched below.
  (cd "$MODELS_FROM" && tar cf - --exclude=./llama-server --exclude=./llama.cpp --exclude=./winsw .) \
    | (cd "$PREFIX/models" && tar xf -)
fi
"$PREFIX/.venv/bin/python" -m tools.fetch_models --models "$PREFIX/models" --no-small

say "configuration and data in $DATA"
mkdir -p "$DATA"
"$PREFIX/.venv/bin/python" -m tools.setup_centre --data "$DATA" --server
chown -R "$USER_NAME:$USER_NAME" "$DATA"
chmod 750 "$DATA"

say "service"
install -m 0644 "$HERE/deploy/linux/voice-exam.service" /etc/systemd/system/voice-exam.service
systemctl daemon-reload
systemctl enable --now voice-exam

ADDRESS="$(hostname -I 2>/dev/null | awk '{print $1}')"
say "done"
echo "The engine starts with the server and restarts itself. It is starting now (up to a minute)."
echo "  Status:       systemctl status voice-exam      Logs: journalctl -u voice-exam -f"
echo "  Exam app:     http://${ADDRESS:-SERVER}:8765/exam/   (open TCP 8765 in the firewall)"
echo "  Device token: sudo voice-exam token"
echo "  Papers:       sudo voice-exam store import PAPER.zip, then sudo voice-exam store open EXAM_ID"
echo "  HTTPS (needed for audio mode): sudo voice-exam certificate --name NAME --ip ${ADDRESS:-ADDRESS}"
