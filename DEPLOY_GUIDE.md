# ASH — full deployment guide

This covers three things you asked about: (1) getting the panel live on ICP,
(2) how GPU power maps to token rewards, and (3) how to hand the whole
system to an agent so it can buy compute and burn it. There's a companion
`AGENTS.md` written *for the agent itself* — you give it that file; this one
is for you.

--------------------------------------------------------------------------
## Part 1 — Get the site live on the Internet Computer

**Install dfx** (the IC toolchain), on the machine that'll deploy:
```bash
sh -ci "$(curl -fsSL https://internetcomputer.org/install.sh)"
```

**Build + rehearse locally:**
```bash
cd ash-v1/frontend
node build.js                 # assembles the single-file dist/index.html
dfx start --background
dfx deploy                    # prints http://<canister-id>.localhost:4943
```
Open that URL, confirm the panel loads.

**Fund with cycles** (ICP's gas — a 42 KB site costs pennies):
```bash
dfx ledger account-id                         # send ~0.5 ICP here from any exchange
dfx cycles convert --amount 0.5 --network ic
dfx cycles balance --network ic               # confirm
```

**Deploy to mainnet:**
```bash
dfx deploy --network ic                       # → https://<canister-id>.icp0.io
```
That URL is permanent and served from the blockchain itself. Redeploys are
just `dfx deploy --network ic` again.

**Connect it to your token** — the panel ships pointing at nothing. After
you deploy the ASH contract (Part 3 / `AGENTS.md` §2), share links like:
```
https://<canister-id>.icp0.io/?net=bittensor-test&contract=0xYOUR_ASH_ADDRESS
```
or paste the address into the panel's settings drawer.

**Keep control:** back up `~/.config/dfx/identity/` — that key *is* control
of the canister. A custom domain (e.g. ash.yourdomain.com) is possible
later via a CNAME plus a `.well-known/ic-domains` file.

The deliberate architecture: **ICP hosts the interface** (censorship-
resistant, no server to seize), the **token lives on the EVM chain**, and
the **visitor's browser bridges** the two by signing with their own wallet.

--------------------------------------------------------------------------
## Part 2 — Does a stronger GPU earn more ASH? Yes — here's exactly how

**Short answer:** more hashrate → linearly more expected ASH, but only
*relative to everyone else mining that epoch*. A bigger GPU wins a bigger
slice of a fixed pool; it does not create more ASH.

**The mechanism, precisely:**

Each epoch (600 seconds) the contract pays a **fixed pool** — 50 ASH at the
start, halving every 210,000 epochs. That pool is split **pro-rata by
shares**:

```
your ASH this epoch = 50 ASH × (your valid shares / all valid shares this epoch)
```

A "share" is one nonce satisfying `keccak(seed ‖ your address ‖ nonce) <
target`. Finding shares is pure brute force, so your **expected shares per
epoch ≈ your hashrate × 600s × p**, where `p` is the per-hash success
probability set by the current target. Double your hashrate → double your
expected shares → double your slice. That's the linear reward for GPU
power.

**But it's zero-sum within the epoch.** Because the pool is fixed, if you
bring a monster rig you don't mint more total ASH — you take a larger
fraction and everyone else's slice shrinks. Ten people with identical GPUs
each earn ~5 ASH/epoch; if one of them 10×'s their hashrate, they earn ~26
and the others drop to ~2.6 each. Same 50 ASH either way.

**Two governors bound this:**

- **The difficulty retarget** keeps the *whole network* near ~512 shares
  per epoch. As total hashrate rises, the target tightens so each share
  represents more burned hashes. Your reward still depends only on your
  *fraction* of network hashrate — not your absolute number — so difficulty
  never changes the fairness, only the "exchange rate" of hashes→shares.
- **The per-epoch share cap (8,192 shares)** stops a sudden whale from
  running away with an epoch before difficulty reacts. Past the cap, extra
  hashrate that epoch is simply wasted — a hard ceiling on how much any
  single burst can grab.

**Why GPUs dominate CPUs here:** keccak is compute-bound with no
memory-hardness, so a GPU out-hashes a CPU by ~10,000× (a 4090 does
gigahashes/sec vs ~200 kH/s on CPU). In a pro-rata race, that ratio *is*
your share ratio — CPU miners earn a rounding error against any real GPU.

**The one caveat you must know:** the bundled miner grinds keccak on **CPU**
today (it's the reference implementation). Renting an expensive GPU right
now runs that CPU loop and wastes the GPU. Wiring a GPU keccak kernel is a
documented, well-bounded task (the preimage to grind is fixed:
`seed(32) ‖ address(20) ‖ nonce(32, big-endian)`, pass when the hash <
target) — any stock GPU keccak miner adapts to it. Until that's done, mine
on hardware you already pay for (`--market local`), or at genesis/eased
difficulty where even CPU or in-browser hashing lands shares. This is
called out prominently in `AGENTS.md` so your agent won't burn money
renting GPUs it can't yet use at full speed.

--------------------------------------------------------------------------
## Part 3 — Hand it to an agent so it can buy + burn compute

The whole point: give an agent a Bittensor wallet and `AGENTS.md`, and it
can fund itself, rent compute, burn it, and claim ASH. The system has three
layers so this is as simple as possible:

**Layer 1 — the CLI (`py/ash.py`).** One command surface:
```bash
python3 py/ash.py provision       # make all 3 wallets: Bittensor + ICP + EVM
python3 py/ash.py wallets         # print the 2 fundable addresses, cleanly
python3 py/ash.py identity        # the EVM address it mines with (auto-created)
python3 py/ash.py fund-address    # SS58 address to send TAO to + the btcli command
python3 py/ash.py status          # epoch, difficulty, its balance, its gas
python3 py/ash.py burn --market local --epochs 5    # burn compute → shares → claim
python3 py/ash.py claim           # sweep any unclaimed epochs
python3 py/ash.py withdraw --to <ss58> --amount 0.1 # pull gas back to coldkey
```

**Layer 2 — the MCP server (`py/mcp_server.py`).** Register it in your
agent's MCP config and it gets native tools — `ash_provision`,
`ash_wallets`, `ash_identity`, `ash_fund_address`, `ash_status`,
`ash_burn`, `ash_claim`, `ash_withdraw` — each returning structured JSON.
`ash_wallets` hands back the fundable message to relay to you. No shelling
out, no parsing:
```json
{ "mcpServers": {
    "ash": { "command": "python3", "args": ["/ABS/PATH/ash-v1/py/mcp_server.py"] } } }
```

**Layer 3 — the brief (`AGENTS.md`).** A self-contained runbook the agent
reads to do everything: install, verify the build, deploy the contract
(testnet-first), host the panel, fund its identity, and run the burn loop —
with the GPU-economics caveat baked in so it makes sound rent/skip choices.

**The funding bridge (how the Bittensor wallet connects):** the agent's
mining key is an EVM (H160) address. Its matching "mirror" SS58 address is
`SS58(blake2_256("evm:" ‖ address), prefix 42)` — verified in this repo
against the canonical Substrate test vector. You send TAO to that SS58 with
`btcli wallet transfer`, and it lands as gas on the EVM address. `ash.py
fund-address` computes it and prints the exact command. Only ~0.2–0.5 TAO
is needed — that's transaction gas, not rental; GPU rental is billed
through Targon/Lium's own API credits.

**The linking flow you'd actually run:**
1. Drop `ash-v1/` on the agent's machine, give it `AGENTS.md`.
2. Agent runs `python3 py/ash.py provision` — creating a Bittensor coldkey,
   an ICP identity, and its EVM mining key in one step.
3. Agent runs `python3 py/ash.py wallets` and **sends you a clean standalone
   message with the two addresses to fund**:
   - a Bittensor **SS58 address** → send ~0.5 TAO (compute + gas)
   - an ICP **account ID** → send ~0.5 ICP (hosting cycles)
   Two clicks, done.
4. You fund both. The agent confirms arrival (`status`), then runs
   `burn --market local` (or `targon`/`lium` with an API key) and starts
   minting ASH, claiming as epochs close.

Simplest possible start is `--market local` — burns the agent's own
hardware, needs no API keys at all. Rental markets are an upgrade once the
GPU kernel is wired.

--------------------------------------------------------------------------
## What's proven vs what needs a live run

Verified offline in this repo: the contract compiles and passes 12 tests on
its real bytecode; the retarget/supply laws hold under a 2,500-epoch stress
sim; the miner, agent CLI, MCP server, and web panel all pass their suites;
keccak/calldata/SS58 derivations are byte-exact against independent oracles.

Needs a live chain/account (can't exist until you deploy): the actual
testnet deployment, a real wallet funding round-trip, the GPU kernel, and
an independent security audit before the token holds real value. These are
the deploy checklist, spelled out in `AGENTS.md`.
