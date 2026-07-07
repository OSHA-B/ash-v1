# ASH — proof of burned compute

A zero-utility, ownerless, mineable ERC-20. The only way ASH exists is that
somebody pointed a GPU at a keccak puzzle and burned electricity — ideally a
GPU **rented from decentralized compute markets** (Targon SN4, Lium SN51),
turning idle subnet capacity into the furnace behind the token.

```
agent loop, once per epoch:
    quote GPU spot  →  EV = pool × my_share − rent  →  rent or skip
    if rented:  grind keccak(seed ‖ my_address ‖ nonce) < target on the pod
                submitShares(nonces)                        (on-chain, ~free to verify)
    epoch settles: fixed pool split pro-rata by shares  →  claim() mints ASH
```

The token does nothing. No staking, no governance, no yield, no roadmap, no
owner. Each unit is a receipt for compute that was destroyed and cannot be
recovered. Utility is a bug. Scarcity is the feature.

## Why v1 (economic tilt) and not alpha-burn gating

An immutable contract must depend on **nothing that can die**. Hard-coding
Targon/Lium alpha into frozen code means the contract bricks forever if
either subnet migrates, renames, or deregisters. Renting the GPU already
routes value to the compute subnets — that *is* the integration. So the
venue claim is enforced the way Bitcoin enforces venue: by price, not by
code. When emission-subsidized rental is the cheapest marginal FLOP, rented
decentralized GPU is where rational hashrate lives.

## The frozen rules (contracts/ASH.sol — 5,912 bytes deployed)

| Rule | Value | Why it can be frozen |
|---|---|---|
| Owner / admin / upgrade | **none** | nothing to capture, ever |
| External dependencies | **none** | keccak256 + block.timestamp only |
| Epoch | 600 s | |
| Pool | 50 ASH / epoch, halves every 210,000 epochs (~4 y) | emission is time-scheduled |
| Hard cap | 21,000,000 ASH | schedule sums to cap − 6,090,000 wei; `claim()` re-checks anyway |
| Empty epoch | mints **nothing, forever** | deflationary, no deadlock path |
| Work | `keccak(seed ‖ msg.sender ‖ nonce) < target` | address-bound → un-stealable from mempool |
| Seed | chained on prior epoch's final share count | future epochs not precomputable while anyone mines |
| Retarget | bang-bang ×2/÷2, +1 doubling per idle epoch, clamped [2^32, 2^248] | throttles **gas, not emission** → crude is safe; idle time eases the puzzle until a laptop can mine → **deadlock impossible** |
| SHARE_CAP | 8,192 shares/epoch | bounds worst-case state under hashrate spikes (found by the chaos sim — see below) |
| Nonce rule | strictly increasing per miner per epoch | replay guard for one warm SSTORE per batch |
| Claims | pro-rata, never expire | |

## What was verified in this repo (all runnable)

- **Compiles clean** on solc 0.8.24, optimizer 5,000 runs (`node compile.js`).
- **12 tests executed against the real compiled bytecode** in an in-process
  EVM (`node evm_test.js`): mining with real keccak, exact pro-rata claims
  (37.5 / 12.5 of a 50 pool), replay / fake-work / live-epoch / double-claim
  guards, `frontier()` == post-`roll()` state, idle easing with MAX_TARGET
  clamp, sole-miner full pool, ERC-20 conservation, on-chain halving math.
- **Byte-exact parity** between the Python mirror and Solidity's
  `abi.encodePacked` for both preimages (share + reseed), cross-checked via
  ethers' independent encoder (`parity_vectors.json`).
- **2,500-epoch chaos sim** (`py/longrun_sim.py`): 1000× hashrate swings, a
  60-epoch total miner exodus (recovery: 1 epoch after hashrate returned),
  share counts bounded, exact integer supply arithmetic.
- **End-to-end vertical slice** (`py/agent_demo.py`): an agent quotes GPU
  spot, rents only when EV > 0, mines real shares, epochs settle, claims pay.

The chaos sim earned its keep: it caught that a sudden 1000× whale could
land ~3.2M shares in one epoch before the once-per-epoch halving reacts —
unbounded state growth in a frozen contract. Fix: `SHARE_CAP` (16× target).
Past the cap the epoch is full; difficulty catches up on later rolls.
Bounded state forever beats perfect fairness during a spike.

## Quickstart

```bash
npm install                      # solc-js, ethers, ethereumjs (dev only)
node compile.js                  # compile → out_ASH.abi.json / out_ASH.bin
node evm_test.js                 # 12 tests on the real bytecode
pip install pycryptodome
python3 py/agent_demo.py         # agent × GPU market × contract, offline
python3 py/live_loop.py --dry-run --epochs 4   # the production loop, offline
node frontend/test.js            # frontend lib: keccak/calldata parity
cd frontend && node build.js     # → dist/index.html (ICP asset canister)
python3 py/longrun_sim.py        # 2,500-epoch stress + supply law
python3 py/miner.py              # reference miner benchmark
```

## Live wiring (added after the scaffold — routes from official docs, 2026-07-07)

**Chain, verified:** Bittensor EVM mainnet = chainId **964**, RPC
`https://lite.chain.opentensor.ai`; testnet = chainId **945**,
`https://test.chain.opentensor.ai` (docs.learnbittensor.org, chainid.network).
Gas in TAO — the token lives where the compute economy lives. Base works
identically as an alternative.

**Targon (SN4), wired over documented REST** (`docs.targon.com/api/workloads`,
base `api.targon.com`, Bearer `TARGON_API_KEY`): register RENTAL workload
with the miner inlined as `commands` → deploy → poll `/logs` for `NONCE`
lines → DELETE at epoch end. Bonus from the register-then-deploy pattern:
registering commits nothing, so `quote()` registers a throwaway workload,
reads exact `cost_per_hour`, and deletes it — a free, precise quote.

**Lium (SN51), wired over documented surface**: pricing from
`https://lium.io/api/machines` (docs call it authoritative); pod lifecycle
via the official CLI (`pip install lium.io && lium init`, then
`up --gpu … --ttl` → `scp pod_miner.py` → `exec` streaming stdout → `rm`).

**Zero key custody on rented hardware** (`py/pod_miner.py`): the pod only
ever sees (seed, address, target) — all public — and emits address-bound
nonces. They only verify when submitted *from* that address, so a malicious
host can't steal work, only withhold it (worst case: lost rent). Lium's own
docs warn against putting keys on pods; this design never needs to. The pod
miner is dependency-free — it embeds a pure-python keccak verified
byte-exact against pycryptodome across 209 cases including all padding
boundaries.

**The production loop** (`py/live_loop.py`): frontier → mine via any market
→ submit shares *as found* in chunks (time-aware flush before the flip;
unsubmitted shares die at the flip) → claim finished epochs. The `--dry-run`
mode runs the *identical loop code* against the simulated contract with
real hashing — and it already earned its keep: it caught that small share
buffers flushed only after the flip (all work silently lost) and that a
poll-then-submit race at the boundary crashed instead of degrading. Both
fixed; the loop now drops stale shares gracefully and stands down when an
epoch hits SHARE_CAP.

## From wiring to mainnet — the honest gap list

1. **Adapters are wired from official docs but not yet exercised against
   live accounts.** Expect field-level fixes on first run (a renamed JSON
   key, a CLI flag), not architectural ones. First run: `quote()` only,
   then one short rental, then a full epoch.
2. **GPU kernel.** `pod_miner.py` is the CPU reference (~150–200 kH/s).
   Real mining swaps its loop for a GPU keccak grinder over the same
   84-byte preimage `seed(32) ‖ address(20) ‖ nonce(32, BE)` — the stdout
   protocol and everything around it stays identical. Hashrate per GPU is
   deliberately unpublished guesswork: calibrate from the pod's `HASHRATE`
   lines and pin numbers into `EST_HASHRATE`.
3. **Deploy = freeze.** `node deploy.js` with `RPC_URL`/`PRIVATE_KEY`
   (testnet 945 first), verify source on an explorer, publish the address,
   round-trip one share with `live_loop.py` before telling anyone. There is
   no owner to renounce — the deployer holds nothing and can do nothing.
4. **`py/chain.py`, `deploy.js`, `watcher.py` are untested against a live
   node** — testnet shakes them out in an afternoon.
5. **Audit before real value.** The suite here is strong for a scaffold and
   is not a substitute for independent review of an immutable contract.
6. A tradeable token launch has regulatory surface that depends on where
   and how you distribute. Check before mainnet.

## Files

```
contracts/ASH.sol      the whole protocol (one file, no imports)
compile.js             solc 0.8.24 build → ABI + bytecode
evm_test.js            12-test suite executed on the real bytecode
deploy.js              ethers v6 deploy (testnet 945 / mainnet 964)
parity_vectors.json    Solidity↔Python byte-parity vectors
py/ash_sim.py          exact Python mirror of the contract
py/miner.py            reference keccak miner (+ hashrate bench)
py/pod_miner.py        dependency-free miner that runs ON rented pods
py/adapters.py         Targon REST · Lium API+CLI · Mock — one interface
py/live_loop.py        production mining loop (+ tested --dry-run mode)
py/agent_demo.py       rent-arbitrage agent, end-to-end offline
py/longrun_sim.py      2,500-epoch chaos + supply-law verification
py/chain.py            web3 client for a live deployment
py/ash.py              agent CLI: identity · fund-address · burn · claim · withdraw
py/mcp_server.py       MCP server exposing ash_* tools to any agent
py/watcher.py          epoch/supply → Telegram monitor (curl-style ops)
AGENTS.md              self-contained runbook an agent reads to deploy + mine
DEPLOY_GUIDE.md        human guide: ICP hosting · GPU-reward mechanics · agent linking
frontend/              furnace panel — single-file dapp, ICP-ready
frontend/dfx.json        asset canister config (dfx deploy --network ic)
frontend/src/            ashlib.js · app.js · worker.js · template.html
frontend/dist/           built index.html + .ic-assets.json5 headers
frontend/test.js         keccak/calldata parity + DOM-stubbed smoke test
frontend/DEPLOY.md       ICP runbook (local replica → mainnet)
```

## Frontend — the furnace panel (ICP-hosted)

A single 42 KB self-contained page, served from an ICP asset canister,
speaking raw JSON-RPC to the ASH contract: live epoch gauge, difficulty,
pool, supply-vs-cap, an in-page keccak burner (WebWorker, ~5 kH/s —
demo-grade by design, real hashrate is the pod/GPU miner), share
submission and claim scanning through the visitor's own wallet. Zero
dependencies, zero CDNs, zero keys on the page. Its keccak, share
preimage, and calldata are all verified byte-exact against the same
oracles as everything else in this repo. Deploy: `frontend/DEPLOY.md`.
