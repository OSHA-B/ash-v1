#!/usr/bin/env bash
# mine_epoch.sh — mine exactly 1 ASH epoch and claim, then exit.
# Called by cron ~7x/day to spread gas over ~30 days.
#
# MARKET selection (set env or edit default below):
#   MARKET=local    — CPU-only, no API key needed (default)
#   MARKET=lium     — SN51 GPU rental (needs: lium init + lium topup)
#
# GPU selection:
#   LIUM_GPU=A100   (cheapest capable GPU; options: A30, A40, RTX4090, H100)
set -e

export ASH_NETWORK=bittensor
export ASH_RPC=https://lite.chain.opentensor.ai
export ASH_CONTRACT=0xA0EadE44e10C433E253aADd073cdFEd6af97F43A

LOGFILE="$HOME/ash-v1/logs/burn.log"
PYTHON=$(command -v python3.11 2>/dev/null || echo python3.11)
MARKET="${MARKET:-local}"
LIUM_GPU="${LIUM_GPU:-A100}"

mkdir -p "$(dirname "$LOGFILE")"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] starting 1-epoch burn (market: $MARKET)" >> "$LOGFILE"

cd "$HOME/ash-v1"

case "$MARKET" in
  local)
    "$PYTHON" py/ash.py burn --market local --epochs 1 >> "$LOGFILE" 2>&1
    ;;
  lium)
    # Prereq: lium init (one-time setup) + lium topup (add TAO balance)
    # Check balance: lium balance
    "$PYTHON" py/ash.py burn --market lium --gpu "$LIUM_GPU" --epochs 1 >> "$LOGFILE" 2>&1
    ;;
  *)
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: unknown MARKET=$MARKET (valid: local, lium)" >> "$LOGFILE"
    exit 1
    ;;
esac

echo "[$(date '+%Y-%m-%d %H:%M:%S')] done (market: $MARKET)" >> "$LOGFILE"
