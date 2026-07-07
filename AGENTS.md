# AGENTS.md — operating ASH, for an autonomous agent

You are an agent with access to a Bittensor wallet. This file tells you how
to stand up **ASH** (a proof-of-burned-compute token) and how to actually
burn compute to mine it. Read the whole file once, then act. Everything
here has been tested except the steps explicitly marked LIVE-UNTESTED
(they need a real chain/account, which only exists at deploy time).

Repo layout you were given: `ash-v1/` (contract + engine + agent CLI +
MCP server) and `ash-panel.html` (a standalone build of the web panel).
Work inside `ash-v1/`.

--------------------------------------------------------------------------
## 0. What ASH is (so your decisions are sound)

- ASH is a real ERC-20 on an EVM chain (Bittensor EVM, chainId 964; or
  Base). Not synthetic, not a derivative. Fully transferable.
- The ONLY way it mints: submit a nonce where
  `keccak256(seed ‖ yourAddress ‖ nonce) < target`. Finding one costs real
  hashing (burned compute); verifying costs the contract one hash.
- Emission is a fixed clock: **50 ASH per 600-second epoch**, split
  pro-rata among all valid shares that epoch, halving every 210,000
  epochs, hard cap 21,000,000. Empty epochs mint nothing, forever.
- **No owner, no admin, no upgrades.** Once deployed it is frozen. The
  deployer (you) receives no premine.
- Shares are ADDRESS-BOUND: a nonce only counts when submitted from the
  address in its preimage. This is why you can mine on rented/untrusted
  hardware safely — a hostile host can withhold your nonces but can never
  steal them.

--------------------------------------------------------------------------
## 1. Install (once)

```bash
cd ash-v1
# Node toolchain for compiling/deploying the contract + building the panel
npm install
# Python deps for the miner engine, agent CLI, and chain calls
pip install pycryptodome web3 eth-account substrate-interface --break-system-packages
# Bittensor CLI (for the coldkey that holds TAO) and IC SDK (for hosting)
pip install bittensor-cli --break-system-packages
sh -ci "$(curl -fsSL https://internetcomputer.org/install.sh)"
```

Verify the build is sound before trusting it (all of this passes offline):

```bash
node compile.js                     # contract compiles → out_ASH.abi.json / .bin
node evm_test.js                    # 12 tests on the real bytecode
python3 py/longrun_sim.py           # retarget + supply-law stress
node frontend/test.js               # frontend keccak/calldata parity
python3 py/live_loop.py --dry-run --epochs 3   # the full mining loop, offline
```

If any of these fail, STOP and report — do not deploy a contract that
doesn't pass its own suite.

--------------------------------------------------------------------------
## 2. Provision wallets, then report both addresses back (DO THIS FIRST)

Before anything can happen, the operator has to fund two wallets. Create
all of them in one command:

```bash
python3 py/ash.py provision
```

This creates: a **Bittensor coldkey** (holds TAO — pays for compute and
gas), an **ICP identity + account** (holds ICP — pays for hosting cycles),
and the **EVM mining key** (auto, gassed from the Bittensor side). Keys are
stored 0600 in `~/.ash`. `btcli` prints the Bittensor mnemonic **once** —
it is the operator's to secure; never store, log, or repeat it.

Then print the two fundable addresses:

```bash
python3 py/ash.py wallets
```

**⇒ Take the `fundable_message` string from that output and send it to the
user as its OWN separate message, verbatim.** It looks like this and is
designed so they fund both wallets in two clicks:

```
═══════════════════════════════════════════════════════
  ASH is provisioned. Fund these two wallets to begin.
═══════════════════════════════════════════════════════

① BITTENSOR  — send TAO here (pays for compute + gas)
   Address (SS58):  5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy
   Fund with ~0.5 TAO:
     btcli wallet transfer --dest 5DAAnrj7…TXFy --amount 0.5 --network test

② INTERNET COMPUTER  — send ICP here (pays for hosting/cycles)
   Account ID:  a3d5e7f9…5c6d
   Principal:   un4fu-tqaaa-aaaab-qadjq-cai
   Fund with ~0.5 ICP from any exchange to the Account ID above.
═══════════════════════════════════════════════════════
```

Do not bury these addresses inside a long status update — the user needs
them clean and on their own. After you've sent that message, wait for the
operator to fund, then continue. Confirm arrival with `python3 py/ash.py
status` (EVM gas balance) before mining, and check the ICP account in the
NNS/wallet before `dfx deploy --network ic`.

--------------------------------------------------------------------------
## 3. Deploy the token (LIVE-UNTESTED — do testnet first)

Deploying is irreversible: **deploy = freeze**. Testnet before mainnet, no
exceptions.

```bash
# TESTNET (chainId 945)
export RPC_URL=https://test.chain.opentensor.ai
export PRIVATE_KEY=0x<a funded EVM key you control>   # provisioned + funded via §2
export INITIAL_TARGET=0            # 0 = default ~1-in-2^20, easy genesis
node deploy.js
# → prints "ASH deployed — and frozen — at 0x…"  ← save this address
```

Then round-trip ONE share on testnet before considering mainnet:

```bash
export ASH_NETWORK=bittensor-test
export ASH_CONTRACT=0x<the address you just deployed>
python3 py/ash.py status           # confirm epoch advances, gas present
python3 py/ash.py burn --market local --epochs 2   # mine + submit + claim
```

Mainnet is identical with `RPC_URL=https://lite.chain.opentensor.ai` and
chainId 964. After deploying, verify the source on the chain's explorer so
others can trust it, and publish the address.

--------------------------------------------------------------------------
## 4. Host the panel on ICP (LIVE-UNTESTED — needs cycles)

The panel is a single self-contained page served from an ICP asset
canister. It reads the contract over JSON-RPC and writes through a
visitor's browser wallet; it holds no keys.

```bash
cd frontend
node build.js                       # src/ → dist/index.html
dfx start --background && dfx deploy # LOCAL rehearsal → prints a localhost URL
```

For IC mainnet you need cycles (ICP's gas):

```bash
dfx identity get-principal          # your controller identity
dfx ledger account-id               # send ~0.5 ICP here from an exchange
dfx cycles convert --amount 0.5 --network ic
dfx deploy --network ic             # → https://<canister-id>.icp0.io
```

Point the panel at the token by sharing:
`https://<canister-id>.icp0.io/?net=bittensor-test&contract=0x<ASH>`
(Back up `~/.config/dfx/identity/` — that key controls the canister.)

--------------------------------------------------------------------------
## 5. Fund your mining identity — details of the Bittensor bridge

Your miner uses a local EVM key (created automatically, stored 0600 in
`~/.ash/evm_key.json`). To give it gas from your Bittensor TAO:

```bash
python3 py/ash.py identity          # shows your EVM address
python3 py/ash.py fund-address      # shows the SS58 "mirror" + btcli command
```

`fund-address` prints something like:

```json
{ "send_tao_to_ss58": "5E1x…", "for_evm_address": "0xef14…",
  "btcli_command": "btcli wallet transfer --dest 5E1x… --amount 0.5" }
```

Run that btcli command from wherever your coldkey lives. The SS58 mirror is
derived as `SS58(blake2_256("evm:" ‖ address), prefix 42)` — TAO sent there
appears as gas on your EVM address (this derivation is verified against the
canonical Substrate vector). **~0.2–0.5 TAO is plenty** — it only pays
per-transaction gas (submitShares / claim), not GPU rental. Rental is paid
through the market's own API credits (see §5).

To pull leftover gas back out later:
`python3 py/ash.py withdraw --to <your-ss58-coldkey> --amount 0.1`

--------------------------------------------------------------------------
## 6. Burn compute → mine ASH (the main loop)

Three markets, in increasing setup cost:

**`local`** — burn your own CPU/GPU, zero API keys. Simplest possible start.
```bash
export ASH_NETWORK=bittensor-test ASH_CONTRACT=0x<ASH>
python3 py/ash.py burn --market local --epochs 5
```

**`targon`** (SN4) — rent GPUs via the Targon REST API.
```bash
export TARGON_API_KEY=<key from targon.com>
python3 py/ash.py burn --market targon --epochs 5 --resource h200-small
```

**`lium`** (SN51) — rent GPUs via the Lium CLI.
```bash
pip install lium.io && lium init      # sets Lium API key + SSH keys
python3 py/ash.py burn --market lium --epochs 5 --gpu A100
```

The loop, per epoch: read `frontier()` → mine `keccak(seed ‖ addr ‖ nonce)
< target` → submit shares as found in ≤64 batches (unsubmitted shares die
at the epoch flip — this is handled: stale shares are dropped, the miner
retunes) → claim finished epochs. Then:

```bash
python3 py/ash.py claim               # sweep anything still unclaimed
python3 py/ash.py status              # see your ASH balance
```

### ⚠️ CRITICAL honest limitation you must account for

**The bundled miner grinds keccak on CPU** (`pod_miner.py`, ~150–200 kH/s).
It is a correctness reference, not a fast miner. If you rent an H200 today,
the rental runs this CPU loop and the expensive GPU sits idle — you will
almost certainly spend more on rent than you earn. Do NOT rent GPUs for
profit until a GPU keccak kernel is wired in. The preimage a kernel must
grind is fixed and simple: `seed(32 bytes) ‖ address(20) ‖ nonce(32, big-
endian)`, pass when the keccak256 output as a big-endian uint256 is `<
target`. Any stock GPU keccak grinder adapts by swapping this 84-byte
preimage; the stdout protocol (`NONCE <n>` lines) stays identical and
everything downstream already works. Until then, prefer `--market local`
on hardware you already pay for, or mine at genesis/eased difficulty where
even CPU/browser hashing lands shares.

--------------------------------------------------------------------------
## 7. Optional: expose yourself as MCP tools

If you're an MCP-capable agent, register the server so you get native
`ash_*` tools instead of shelling out:

```json
{ "mcpServers": {
    "ash": { "command": "python3", "args": ["/ABS/PATH/ash-v1/py/mcp_server.py"] } } }
```

Tools: `ash_provision`, `ash_wallets`, `ash_identity`, `ash_fund_address`,
`ash_status`, `ash_burn` (args: market, epochs), `ash_claim`,
`ash_withdraw`. Each returns structured JSON. `ash_wallets` returns the
`fundable_message` to relay to the user as a standalone message. For an unbounded miner, run
`python3 py/ash.py burn` under a process manager rather than as one tool
call.

--------------------------------------------------------------------------
## 8. Environment variables (your whole config surface)

| var | default | meaning |
|---|---|---|
| `ASH_HOME` | `~/.ash` | where your EVM key is stored (0600) |
| `ASH_NETWORK` | `bittensor-test` | `bittensor` \| `bittensor-test` \| `base` |
| `ASH_RPC` | network preset | override the RPC URL |
| `ASH_CONTRACT` | — | the deployed ASH address (needed for burn/claim/status) |
| `TARGON_API_KEY` | — | only for `--market targon` |
| `RPC_URL`,`PRIVATE_KEY`,`INITIAL_TARGET` | — | only for `deploy.js` |

--------------------------------------------------------------------------
## 9. A sensible first run, end to end

```bash
cd ash-v1 && npm install
pip install pycryptodome web3 eth-account substrate-interface bittensor-cli --break-system-packages
node evm_test.js                                    # 1. trust the contract
python3 py/ash.py provision                         # 2. make all wallets
python3 py/ash.py wallets                           # 3. ⇒ SEND fundable_message to the user, its own message
#   … wait for the operator to fund the two wallets …
python3 py/ash.py status                            # 4. confirm EVM gas arrived
node deploy.js  # (testnet RPC_URL + the funded EVM key)   5. freeze the contract
export ASH_NETWORK=bittensor-test ASH_CONTRACT=0x<deployed>
python3 py/ash.py burn --market local --epochs 5    # 6. burn → shares → claim
python3 py/ash.py status                            # 7. balance went up
dfx deploy --network ic  # (from frontend/)         # 8. host the panel, point it at ASH_CONTRACT
```

Report back (as a clean summary): the two fundable addresses FIRST as their
own message (via `ash_wallets`), then after deployment — the deployed
contract address, your EVM mining address, ASH balance after the run, and
the panel URL. Flag immediately if any §1 verification
step failed or if the CPU-miner economics (§5) make a requested GPU rental
unprofitable.
