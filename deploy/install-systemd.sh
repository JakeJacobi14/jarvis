#!/usr/bin/env bash
# Install user systemd units so the phone panel (and optionally the voice
# stack) start at boot on a headless Linux box.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"

for name in jarvis-control jarvis; do
  src="$ROOT/deploy/systemd/${name}.service.in"
  dest="$UNIT_DIR/${name}.service"
  sed "s|@JARVIS_ROOT@|$ROOT|g" "$src" >"$dest"
  echo "==> Wrote $dest"
done

# Control panel venv (voice venvs are created by start.sh --setup)
if [[ ! -x "$ROOT/services/control/.venv/bin/python" ]]; then
  echo "==> Creating control venv..."
  python3 -m venv "$ROOT/services/control/.venv"
  "$ROOT/services/control/.venv/bin/pip" install -q --upgrade pip
  "$ROOT/services/control/.venv/bin/pip" install -q -r "$ROOT/services/control/requirements.txt"
fi

systemctl --user daemon-reload
systemctl --user enable --now jarvis-control.service

echo
echo "Phone panel is enabled at boot."
echo "  Optional, start voice at boot too:"
echo "    systemctl --user enable --now jarvis.service"
echo
echo "If this box has no GUI login, linger so user services run at boot:"
echo "    sudo loginctl enable-linger $USER"
echo
echo "Then open  http://$(hostname -I | awk '{print $1}'):${JARVIS_CONTROL_PORT:-8740}"
