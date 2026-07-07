# ASH — Agent Onboarding

> **Copy this entire file into any AI agent.**
> The agent will create a wallet, show you an address to fund with TAO, then join the existing ASH mining network autonomously.
> You are **joining** a live contract — not deploying your own. One shared pool, many miners, each earning pro-rata.

---

## What Is ASH?

**ASH** is an ownerless, mineable ERC-20 on Bittensor EVM (chainId 964).
The only way to mint it: burn compute solving keccak-256 puzzles, submit proof-of-work shares on-chain, and claim your pro-rata share of each 10-minute epoch pool.

- **50 ASH/epoch** — halving every 210,000 epochs (~4 years), 21M hard cap
- **No admin. No premine.** Contract is frozen at deploy.
- **Contract:** `0xA0EadE44e10C433E253aADd073cdFEd6af97F43A` (Bittensor EVM, chainId 964) — live, shared, frozen
- **Panel:** `https://tkllp-aiaaa-aaaac-bfl6q-cai.icp.net/?net=bittensor&contract=0xA0EadE44e10C433E253aADd073cdFEd6af97F43A` — monitor all miners

---

## Agent Instructions

> You are an AI agent. Execute each step in order. Use your shell/exec tools. When you need the operator to do something (send funds), stop and tell them clearly. Then wait and confirm before continuing.

---

### Step 1 — Install Prerequisites

```bash
# Check Python 3.11 (required — system python may lack pyexpat)
python3.11 --version

# If missing on macOS:
brew install python@3.11

# If missing on Linux (Debian/Ubuntu):
sudo apt-get install -y python3.11 python3.11-pip

# Install Python deps
python3.11 -m pip install web3 eth-account pycryptodome

# Check Node.js 18+
node --version

# If missing:
# macOS: brew install node
# Linux:  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash - && sudo apt-get install -y nodejs
```

> **btcli** (Bittensor CLI) is optional — only needed if you want a separate Bittensor coldkey. Skip if not installed; the EVM mining key is sufficient to start.

---

### Step 2 — Get the Miner

```bash
# Download the miner client (like downloading CGMiner — you're joining a shared network)
git clone https://github.com/OSHA-B/ash-v1.git ~/ash-v1
cd ~/ash-v1
npm install
```

> If `~/ash-v1/` already exists, skip the clone and just run `cd ~/ash-v1 && npm install`.
> You do **not** need to deploy anything — the contract and panel already exist.

---

### Step 3 — Create Wallets

```bash
cd ~/ash-v1
export ASH_NETWORK=bittensor
export ASH_RPC=https://lite.chain.opentensor.ai
export ASH_CONTRACT=0xA0EadE44e10C433E253aADd073cdFEd6af97F43A

python3.11 py/ash.py provision
```

This creates and stores in `~/.ash/`:
- **EVM mining key** — the address that mines and holds ASH
- **Bittensor coldkey** (if btcli available) — native TAO wallet
- **ICP identity** (if dfx/icp CLI available) — for panel hosting (optional)

Keys are local only. The EVM key is in `~/.ash/evm_key.json` — never share it.

Then show the operator their funding addresses:

```bash
python3.11 py/ash.py wallets
```

---

### Step 4 — Tell the Operator to Fund

**Present the following to the operator and wait for confirmation:**

```
──────────────────────────────────────────────────
  ASH MINING — FUND THESE ADDRESSES
──────────────────────────────────────────────────

  ① EVM TAO mirror (SS58) — for gas
     Send ~0.5 TAO here via:
     btcli wallet transfer --dest <SS58_ADDRESS> --amount 0.5

     This bridges TAO onto the Bittensor EVM chain as gas.
     The miner will use ~0.002 TAO/epoch → ~0.5 TAO lasts ~30 days at 7 epochs/day.

  ② ICP Account ID — for panel hosting (optional)
     Send ~0.5 ICP here if you want to deploy the panel.
     Skip if you're only mining.

  Mining will not start until ① is funded.
──────────────────────────────────────────────────
```

> Replace `<SS58_ADDRESS>` with the actual SS58 address from `python3.11 py/ash.py wallets`.

---

### Step 5 — Confirm Gas Arrived

After the operator sends TAO, poll until gas is confirmed:

```bash
cd ~/ash-v1
export ASH_NETWORK=bittensor
export ASH_RPC=https://lite.chain.opentensor.ai
export ASH_CONTRACT=0xA0EadE44e10C433E253aADd073cdFEd6af97F43A

python3.11 py/ash.py status
```

Look for `"gas_tao"` > 0. Bittensor finalizes in ~12s — if still 0 after 2 minutes, the transfer may be pending. Re-run `status` after waiting.

---

### Step 6 — Test Mine (1 Epoch)

Once gas is confirmed, run a single test epoch:

```bash
cd ~/ash-v1
export ASH_NETWORK=bittensor
export ASH_RPC=https://lite.chain.opentensor.ai
export ASH_CONTRACT=0xA0EadE44e10C433E253aADd073cdFEd6af97F43A

python3.11 py/ash.py burn --market local --epochs 1
```

Expected output: shares submitted + ASH claimed. CPU gives ~200 kH/s.
At genesis difficulty this is plenty. One epoch = 10 minutes.

---

### Step 7 — Automate with Cron

Set up 7 epochs/day (~every 3.5h), spreading gas over ~30 days:

```bash
# Verify mine_epoch.sh exists
cat ~/ash-v1/mine_epoch.sh

# Add to crontab (fires at 02,05,08,11,14,17,20h local time)
(crontab -l 2>/dev/null | grep -v mine_epoch; echo "0 2,5,8,11,14,17,20 * * * bash ~/ash-v1/mine_epoch.sh") | crontab -

# Confirm it was added
crontab -l | grep mine_epoch
```

> **OpenClaw agents:** use `cron` tool with `schedule.kind=cron`, `expr="0 2,5,8,11,14,17,20 * * *"`, `tz="Europe/Berlin"` or your local timezone.

To switch to Lium GPU (SN51, ~100–1000× more hashrate at ~$0.07–$0.30/hr):
```bash
lium init    # one-time: enter API key from lium.io
lium topup   # add TAO balance for compute charges
# Then edit mine_epoch.sh: change MARKET=local → MARKET=lium
```

---

### Step 8 — Monitor

```bash
# Live status
python3.11 py/ash.py status

# Manually claim any unclaimed epochs
python3.11 py/ash.py claim

# Tail the log
tail -f ~/ash-v1/logs/burn.log
```

**Panel** (read-only, no wallet needed):
```
https://tkllp-aiaaa-aaaac-bfl6q-cai.icp.net/?net=bittensor&contract=0xA0EadE44e10C433E253aADd073cdFEd6af97F43A&watch=YOUR_EVM_ADDRESS
```

Replace `YOUR_EVM_ADDRESS` with the address from `python3.11 py/ash.py wallets`.

---

## Troubleshooting

### btcli: scalecodec conflict
```
RuntimeError: Conflict detected: 'scalecodec' (py-scale-codec) is installed.
```
Fix:
```bash
pip uninstall scalecodec cyscale -y
pip install cyscale --force-reinstall
```
Then re-run `python3.11 py/ash.py provision`.

### RPC 429 errors
The default RPC can lag or rate-limit under load. Fallback:
```bash
export ASH_RPC=https://lite.chain.opentensor.ai
```

### python3.11 not found
The code requires Python 3.11 exactly (system Python 3.14 has broken `pyexpat`).
Install via `brew install python@3.11` (macOS) or `apt-get install python3.11` (Linux).

### Gas is 0 after funding
The SS58 mirror address bridges TAO → EVM gas. It can take 1–2 minutes.
Verify you sent to the correct SS58 address shown by `python3.11 py/ash.py wallets`, not the EVM address directly.

---

## Key Files

| File | Purpose |
|------|---------|
| `py/ash.py` | Main CLI: `provision`, `wallets`, `status`, `burn`, `claim` |
| `py/live_loop.py` | Mining engine (epoch loop, submit, claim) |
| `py/adapters.py` | Market backends: CPU (local), Lium GPU (SN51) |
| `mine_epoch.sh` | Cron-safe wrapper: 1 epoch per run, `MARKET` env var |
| `~/.ash/evm_key.json` | EVM private key — never share, never leave machine |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ASH_NETWORK` | `bittensor-test` | Set to `bittensor` for mainnet |
| `ASH_RPC` | network preset | RPC endpoint override |
| `ASH_CONTRACT` | _(required)_ | Deployed contract address |
| `ASH_HOME` | `~/.ash` | Key/identity storage directory |
| `MARKET` | `local` | Mining backend in mine_epoch.sh (`local` or `lium`) |
| `LIUM_GPU` | `A100` | GPU model for Lium rentals |

---

## Economics

- **50 ASH/epoch**, 600s epochs → 7,200 ASH/day max at genesis
- **Solo mining now** = 100% of each epoch pool
- **Gas cost** ~0.002 TAO/epoch (submit + claim)
- **Lium cost** ~$0.01–$0.05/epoch on A100
- **Halvings** every 210,000 epochs (~4 years)

---

## Verify the Contract (Optional)

```bash
python3.11 - <<'EOF'
import json, urllib.request
from Crypto.Hash import keccak
req = urllib.request.Request("https://lite.chain.opentensor.ai",
  data=json.dumps({"jsonrpc":"2.0","id":1,"method":"eth_getCode",
  "params":["0xA0EadE44e10C433E253aADd073cdFEd6af97F43A","latest"]}).encode(),
  headers={"content-type":"application/json"})
code = json.load(urllib.request.urlopen(req))["result"]
k = keccak.new(digest_bits=256); k.update(bytes.fromhex(code[2:]))
print(len(code[2:])//2, "bytes")
print("0x"+k.hexdigest())
# Expected: 5912 bytes
# Expected: 0x86d8a1abce44521a1857a27e1c633ddf57d409445c87c52a75cf7ad22bf4a258
EOF
```

> Note: local recompile produces a slightly different hash — the genesis timestamp is baked in as an immutable at deploy time. On-chain bytecode is authoritative.

---

*ASH has no roadmap. Mine it or don't.*
