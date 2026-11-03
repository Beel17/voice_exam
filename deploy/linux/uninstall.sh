#!/usr/bin/env bash
# Remove the voice exam engine: the service, the program and the voice-exam command.
#
#   sudo ./deploy/linux/uninstall.sh            # keeps /var/lib/voice-exam (results!)
#   sudo ./deploy/linux/uninstall.sh --purge    # deletes it too
#
# The data folder may hold results not yet uploaded to the cloud (sudo voice-exam sync
# status): it is kept unless --purge is given.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run it with sudo" >&2; exit 1; }
systemctl disable --now voice-exam 2>/dev/null || true
rm -f /etc/systemd/system/voice-exam.service /usr/local/bin/voice-exam
systemctl daemon-reload
rm -rf /opt/voice-exam
if [ "${1:-}" = --purge ]; then
  rm -rf /var/lib/voice-exam
  userdel voiceexam 2>/dev/null || true
  echo "removed, with all data"
else
  echo "removed; the data is kept in /var/lib/voice-exam"
fi
