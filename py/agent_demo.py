"""
agent_demo.py — full vertical slice, offline.

An autonomous agent runs the rent-arbitrage loop against the simulated
contract (exact Solidity mirror) with REAL keccak mining:

  each epoch:
    quote GPU spot  ->  estimate EV of the epoch  ->  rent or skip
    if rented: grind keccak shares against frontier(), submit in batches
  epoch settles pro-rata; agent claims ASH.

Hash budgets are scaled down so the demo runs on CPU in seconds — the
economics, retarget behavior, and contract rules are the real thing.
"""

from __future__ import annotations
import random
from ash_sim import (AshSim, EPOCH_SECONDS, TARGET_SHARES, MAX_BATCH,
                     pool_of, ash)
from miner import mine_shares
from adapters import MockMarket

rng = random.Random(42)

AGENT = bytes.fromhex("a11ce00000000000000000000000000000000a5b")
BG = {
    "bg-miner-1": (bytes.fromhex("b1" * 20), 22_000),   # hash budget / epoch
    "bg-miner-2": (bytes.fromhex("b2" * 20), 34_000),
    "bg-miner-3": (bytes.fromhex("b3" * 20), 14_000),
}
POD_BUDGET = 60_000            # agent's rented pod, hashes / epoch (scaled)
U256 = (1 << 256) - 1


def mock_ash_price(epoch: int) -> float:
    """Volatile mock ASH/USD feed so rent decisions actually flip."""
    return max(0.0012, 0.0042 + 0.0028 * rng.uniform(-1, 1))


def submit_all(sim: AshSim, sender: bytes, nonces: list[int]):
    for i in range(0, len(nonces), MAX_BATCH):
        sim.submit_shares(sender, nonces[i:i + MAX_BATCH])


def run():
    sim = AshSim(initial_target=U256 >> 12)      # 1 share ≈ 4,096 hashes
    market = MockMarket()
    print("=" * 96)
    print("ASH v1 — end-to-end demo: agent × GPU spot market × simulated contract (real keccak PoW)")
    print("=" * 96)
    hdr = f"{'ep':>3} {'target':>8} {'gpu $/hr':>9} {'ASH $':>8} {'EV $/ep':>9} {'decision':>9} {'agent sh':>9} {'total sh':>9} {'agent burn':>11}"
    print(hdr); print("-" * len(hdr))

    prev_total = TARGET_SHARES // 4              # agent's prior for competition
    mined_epochs: dict[bytes, set[int]] = {a: set() for a in
                                           [AGENT] + [v[0] for v in BG.values()]}
    agent_cost = agent_hashes = 0

    for step in range(8):
        e, seed, target = sim.frontier()
        p_share = target / (U256 + 1)

        # ---- background miners grind (real hashing) ----
        total_bg = 0
        for _, (addr, budget) in BG.items():
            nonces, _ = mine_shares(seed, addr, target,
                                    want=10**9, start_nonce=1,
                                    max_hashes=budget)
            if nonces:
                submit_all(sim, addr, nonces)
                mined_epochs[addr].add(e)
                total_bg += len(nonces)

        # ---- agent decision: rent iff EV > 0 ----
        q = market.quote()
        px = mock_ash_price(e)
        my_exp = POD_BUDGET * p_share
        pool_ash = pool_of(e) / 1e18
        exp_rev = pool_ash * (my_exp / (my_exp + max(prev_total, 1))) * px
        cost = q.usd_per_hour * EPOCH_SECONDS / 3600
        ev = exp_rev - cost

        agent_sh = 0
        if ev > 0:
            market.rent(hours=EPOCH_SECONDS / 3600)
            nonces, tried = mine_shares(seed, AGENT, target,
                                        want=10**9, start_nonce=1,
                                        max_hashes=POD_BUDGET)
            agent_hashes += tried
            agent_cost += cost
            if nonces:
                submit_all(sim, AGENT, nonces)
                mined_epochs[AGENT].add(e)
                agent_sh = len(nonces)
        decision = "RENT" if ev > 0 else "skip"

        tot = sim.total_shares.get(e, 0)
        prev_total = max(tot - agent_sh, 1)
        print(f"{e:>3} {'2^'+str(target.bit_length()-1):>8} {q.usd_per_hour:>9.4f} "
              f"{px:>8.4f} {ev:>+9.4f} {decision:>9} {agent_sh:>9} {tot:>9} "
              f"{(f'{agent_hashes:,}H' if decision=='RENT' else ''):>11}")

        # advance one epoch; once, idle through two extra (deadlock easing)
        sim.warp(EPOCH_SECONDS)
        if step == 4:
            sim.warp(2 * EPOCH_SECONDS)
            e2, _, t2 = sim.frontier()
            print(f"  ·· idled {2} empty epochs → frontier eased to 2^{t2.bit_length()-1} (deadlock-proofing live)")

    # ---- settle: everyone claims every epoch they mined ----
    sim.warp(EPOCH_SECONDS)
    print("-" * len(hdr))
    for label, addr in [("oti-agent", AGENT)] + [(k, v[0]) for k, v in BG.items()]:
        got = sum(sim.claim(addr, ep) for ep in sorted(mined_epochs[addr]))
        extra = ""
        if addr == AGENT:
            rev = got / 1e18 * 0.0042
            extra = (f"   | burned {agent_hashes:,} hashes, spent ${agent_cost:.4f}, "
                     f"~${rev:.4f} at mid px")
        print(f"claimed  {label:<11} {ash(got):>18}{extra}")

    print(f"\ntotalSupply = {ash(sim.total_supply)}  "
          f"(≤ Σ pools of mined epochs ✓)")

    # ---- guard checks (must all raise) ----
    checks = []
    try:
        sim.claim(AGENT, min(mined_epochs[AGENT]))
    except ValueError as ex: checks.append(f"double-claim rejected: '{ex}'")
    # mine two genuine shares, submit them, then replay the same nonces
    _, seed, target = sim.frontier()
    good, _ = mine_shares(seed, AGENT, target, want=2, start_nonce=1)
    sim.submit_shares(AGENT, good)
    try:
        sim.submit_shares(AGENT, good)
    except ValueError as ex: checks.append(f"nonce replay rejected: '{ex}'")
    try:
        sim.submit_shares(AGENT, [2**200])
    except ValueError as ex: checks.append(f"fake work rejected: '{ex}'")
    assert sum(sim.balance.values()) == sim.total_supply
    checks.append("Σ balances == totalSupply")
    print("\nguards:")
    for c in checks:
        print("  ✓", c)


if __name__ == "__main__":
    run()
