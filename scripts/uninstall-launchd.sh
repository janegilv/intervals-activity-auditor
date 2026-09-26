#!/bin/bash
# Stop and remove the LaunchAgent installed by install-launchd.sh.
set -euo pipefail

LABEL="${LABEL:-local.intervals_auditor.monitor}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "Removed $LABEL (data, logs and the database are untouched)."
