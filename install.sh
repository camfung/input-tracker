#!/usr/bin/env bash
# Install input-tracker as a user-level systemd service.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="$HOME/.input-tracker"
UNIT_DIR="$HOME/.config/systemd/user"

# Flags
AUTOSTART=""    # "yes" | "no" | "" (ask)
LINGER=""       # "yes" | "no" | "" (ask only if AUTOSTART=yes)
ASSUME_YES="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --autostart)    AUTOSTART="yes"; shift ;;
    --no-autostart) AUTOSTART="no";  shift ;;
    --linger)       LINGER="yes";    shift ;;
    --no-linger)    LINGER="no";     shift ;;
    -y|--yes)       ASSUME_YES="1";  shift ;;
    -h|--help)
      cat <<EOF
Usage: $0 [--autostart|--no-autostart] [--linger|--no-linger] [-y]

  --autostart      enable systemd user service at login (no prompt)
  --no-autostart   start once now, do not enable at login
  --linger         allow service to run when you are logged out
  --no-linger      do not enable linger
  -y, --yes        default-yes for any prompts not pre-answered
EOF
      exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 1 ;;
  esac
done

ask() {
  # ask "question" "default(y|n)"  → echoes yes|no
  local q="$1" def="$2" reply
  if [[ "$ASSUME_YES" == "1" ]]; then
    echo "$( [[ "$def" == "y" ]] && echo yes || echo no )"
    return
  fi
  local hint="[y/N]"
  [[ "$def" == "y" ]] && hint="[Y/n]"
  read -r -p "$q $hint " reply </dev/tty || reply=""
  reply="${reply,,}"
  if [[ -z "$reply" ]]; then reply="$def"; fi
  case "$reply" in
    y|yes) echo yes ;;
    *)     echo no ;;
  esac
}

mkdir -p "$TARGET_DIR" "$UNIT_DIR"

cp "$SRC_DIR/tracker.py" "$TARGET_DIR/tracker.py"
cp "$SRC_DIR/webui.py" "$TARGET_DIR/webui.py"
cp "$SRC_DIR/requirements.txt" "$TARGET_DIR/requirements.txt"

if [[ ! -d "$TARGET_DIR/venv" ]]; then
  python3 -m venv "$TARGET_DIR/venv"
fi
"$TARGET_DIR/venv/bin/pip" install --upgrade pip >/dev/null
"$TARGET_DIR/venv/bin/pip" install -r "$TARGET_DIR/requirements.txt"

cp "$SRC_DIR/input-tracker.service" "$UNIT_DIR/input-tracker.service"
cp "$SRC_DIR/input-tracker-web.service" "$UNIT_DIR/input-tracker-web.service"
systemctl --user daemon-reload

UNITS=(input-tracker.service input-tracker-web.service)

echo
[[ -z "$AUTOSTART" ]] && AUTOSTART="$(ask 'Start input-tracker (daemon + web UI) automatically at login?' y)"

if [[ "$AUTOSTART" == "yes" ]]; then
  systemctl --user enable --now "${UNITS[@]}"
  echo "  -> enabled at login, started now (daemon + web UI)"

  [[ -z "$LINGER" ]] && LINGER="$(ask 'Also keep tracker running when you are logged out (enable user lingering)?' n)"
  if [[ "$LINGER" == "yes" ]]; then
    if loginctl enable-linger "$USER"; then
      echo "  -> lingering enabled"
    else
      echo "  !! could not enable lingering (needs sudo? skip)"
    fi
  fi
else
  systemctl --user start "${UNITS[@]}"
  echo "  -> started for this session only (will NOT autostart at next login)"
  echo "     run 'systemctl --user enable ${UNITS[*]}' later to autostart"
fi

echo
echo "status:"
systemctl --user --no-pager status "${UNITS[@]}" | head -20
echo
echo "view stats:  $TARGET_DIR/venv/bin/python $TARGET_DIR/tracker.py show"
echo "web ui:      http://127.0.0.1:7070  (served by input-tracker-web.service)"
