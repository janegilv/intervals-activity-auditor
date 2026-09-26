#!/bin/bash
# Install (or update) a macOS LaunchAgent that runs `main.py monitor`
# every INTERVAL_MINUTES (default 30) and when you log in. If the Mac was
# asleep at a scheduled time, launchd runs the job once when it wakes.
#
#   scripts/install-launchd.sh            install / reinstall
#   scripts/install-launchd.sh --print    only print the plist
#   INTERVAL_MINUTES=15 scripts/install-launchd.sh
#
# Output of the monitor goes to data/logs/auditor.log; launchd itself
# only captures warnings, errors and crashes in data/logs/launchd.log.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="${LABEL:-local.intervals-activity-auditor.monitor}"
INTERVAL_MINUTES="${INTERVAL_MINUTES:-30}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$PROJECT_DIR/data/logs"

if ! [[ "$INTERVAL_MINUTES" =~ ^[0-9]+$ ]] || (( INTERVAL_MINUTES < 1 || 60 % INTERVAL_MINUTES != 0 )); then
    echo "INTERVAL_MINUTES must divide 60 (e.g. 10, 15, 20, 30, 60), got '$INTERVAL_MINUTES'" >&2
    exit 1
fi

PYTHON=""
for candidate in "$PROJECT_DIR/venv/bin/python" "$PROJECT_DIR/.venv/bin/python"; do
    if [[ -x "$candidate" ]]; then PYTHON="$candidate"; break; fi
done
PYTHON="${PYTHON:-$(command -v python3)}"

minutes=""
for (( m = 0; m < 60; m += INTERVAL_MINUTES )); do
    minutes+="        <dict><key>Minute</key><integer>$m</integer></dict>"$'\n'
done

plist() {
cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$PROJECT_DIR/main.py</string>
        <string>monitor</string>
        <string>--quiet</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>
    <key>StartCalendarInterval</key>
    <array>
$minutes    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>ProcessType</key>
    <string>Background</string>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/launchd.log</string>
</dict>
</plist>
EOF
}

if [[ "${1:-}" == "--print" ]]; then
    plist
    exit 0
fi

echo "Checking that the auditor runs with $PYTHON ..."
"$PYTHON" "$PROJECT_DIR/main.py" status >/dev/null

mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"
plist > "$PLIST"
plutil -lint "$PLIST" >/dev/null

domain="gui/$(id -u)"
launchctl bootout "$domain/$LABEL" 2>/dev/null || true
launchctl bootstrap "$domain" "$PLIST"

echo "Installed $PLIST"
echo "The monitor runs every $INTERVAL_MINUTES minutes (and now, at load)."
echo "  Run it now:  launchctl kickstart -k $domain/$LABEL"
echo "  Status:      launchctl print $domain/$LABEL | head -20"
echo "  Logs:        tail -f $LOG_DIR/auditor.log"
echo "  Remove:      scripts/uninstall-launchd.sh"
