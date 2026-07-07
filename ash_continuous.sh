#!/usr/bin/env bash
# ash_continuous.sh — run forever, one epoch at a time, with restart on failure.
# Designed to be kept alive by launchd (KeepAlive=true).

export ASH_NETWORK=bittensor
export ASH_RPC=https://lite.chain.opentensor.ai
export ASH_CONTRACT=0xA0EadE44e10C433E253aADd073cdFEd6af97F43A

LOGFILE="$HOME/ash-v1/logs/burn.log"
PYTHON=$(command -v python3.11 2>/dev/null || echo /opt/homebrew/opt/python@3.11/bin/python3.11)

mkdir -p "$(dirname "$LOGFILE")"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ash_continuous starting" >> "$LOGFILE"

cd "$HOME/ash-v1"

# Run forever — live_loop handles epoch flips, claiming, retries
exec /opt/homebrew/opt/python@3.11/bin/python3.11 -u py/ash.py burn --market local 2>&1 | tee -a "$LOGFILE"
