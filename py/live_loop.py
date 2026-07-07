"""
live_loop.py — the production ASH mining loop.

    frontier() → start miner (mock / lium / local)
    → submit nonces AS THEY ARE FOUND in ≤64 batches (a nonce not
      submitted before the epoch flips is worthless)
    → at flip: stop session, claim finished epochs, repeat.

Modes:
  --dry-run              offline: simulated contract + in-process miner
                         (this is the tested path; it exercises the exact
                         same loop code the live path runs)
  --rpc URL --contract 0x… --key 0x…   live chain via py/chain.py
  --market mock|lium

Usage:
  python3 live_loop.py --dry-run --epochs 4
  python3 live_loop.py --rpc https://test.chain.opentensor.ai \
      --contract 0x… --key $PK --market lium           # TESTNET FIRST
"""

from __future__ import annotations
import argparse
import time

from ash_sim import AshSim, EPOCH_SECONDS, MAX_BATCH, ash
import adapters


class ChainRevert(Exception):
    pass


# ---------------------------------------------------------------- chains
class SimChain:
    """Offline stand-in with the exact contract rules; time is virtual and
    advances SPEEDUP× so a dry-run epoch takes ~2s of wall clock."""
    SPEEDUP = 300

    def __init__(self):
        self.sim = AshSim(initial_target=((1 << 256) - 1) >> 12)
        self.addr = bytes.fromhex("a11ce00000000000000000000000000000000a5b")
        self._t0 = time.time()

    def _sync(self):
        virt = int((time.time() - self._t0) * self.SPEEDUP)
        self.sim.now = self.sim.genesis + virt

    def frontier(self):
        self._sync()
        return self.sim.frontier()

    def current_epoch(self):
        self._sync()
        return self.sim.current_epoch()

    def seconds_to_flip(self):
        self._sync()
        return EPOCH_SECONDS - ((self.sim.now - self.sim.genesis) % EPOCH_SECONDS)

    def submit(self, nonces):
        self._sync()
        try:
            self.sim.submit_shares(self.addr, nonces)
        except ValueError as e:
            raise ChainRevert(str(e))

    def claimable(self, mined_epochs):
        cur = self.current_epoch()
        return [e for e in mined_epochs
                if e < cur and (e, self.addr) not in self.sim.claimed]

    def claim(self, e):
        return self.sim.claim(self.addr, e)

    def balance(self):
        return self.sim.balance.get(self.addr, 0)


class LiveChain:
    """Thin wrapper over chain.AshChain (web3). UNTESTED against a real
    node — run on TESTNET (chainId 945) before anything real."""

    def __init__(self, rpc, contract, key):
        from chain import AshChain
        self.c = AshChain(rpc, contract, privkey=key)
        from web3 import Web3 as _W3
        self.addr = bytes.fromhex(self.c.acct.address[2:])
        self._me = _W3.to_checksum_address(self.c.acct.address)

    def frontier(self):
        return self.c.frontier()

    def current_epoch(self):
        return self.c.c.functions.currentEpoch().call()

    def seconds_to_flip(self):
        return self.c.c.functions.secondsToNextEpoch().call()

    def submit(self, nonces):
        try:
            self.c.submit_shares(nonces)
        except Exception as e:                       # surfacing revert reasons
            raise ChainRevert(str(e))

    def claimable(self, mined_epochs):
        cur = self.current_epoch()
        me = self._me
        out = []
        for e in mined_epochs:
            if e >= cur:
                continue
            if self.c.c.functions.claimed(e, me).call():
                continue
            if self.c.shares_of(e, me) > 0:
                out.append(e)
        return out

    def claim(self, e):
        self.c.claim_many([e])
        return 0

    def balance(self):
        return self.c.balance(self._me)


# ---------------------------------------------------------------- the loop
SUBMIT_CHUNK = 16          # submit once this many shares are buffered
FLUSH_WINDOW = 45          # …or whenever this close (chain-seconds) to flip
POLL_IDLE    = 20          # seconds between RPC polls when far from flip
POLL_NEAR    = 3           # seconds between polls when inside FLUSH_WINDOW
RPC_MAX_RETRIES = 6        # retry 429/timeout this many times
RPC_RETRY_BASE  = 2.0      # exponential backoff base (seconds)


def _rpc_call(fn, *args, **kwargs):
    """Call fn(*args, **kwargs) with exponential backoff on 429 / connection errors."""
    import requests.exceptions as _rex
    delay = RPC_RETRY_BASE
    for attempt in range(RPC_MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            msg = str(exc).lower()
            is_429 = "429" in msg or "too many requests" in msg or "rate limit" in msg
            is_net = isinstance(exc, (_rex.ConnectionError, _rex.Timeout))
            if (is_429 or is_net) and attempt < RPC_MAX_RETRIES - 1:
                print(f"  RPC {('429' if is_429 else 'net')} error, retry in {delay:.0f}s")
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            raise


CLAIM_SETTLE_SECS = 4    # brief pause after claim so RPC state catches up


def _try_claim(chain, e) -> bool:
    """Attempt to claim epoch e. Returns True if claimed (or already was).
    Silently ignores 'ASH: claimed' revert (double-claim race / RPC lag)."""
    try:
        chain.claim(e)
        return True
    except Exception as exc:
        msg = str(exc)
        if "claimed" in msg.lower():
            print(f"  [claim {e}] already claimed (RPC lag or duplicate) — skipping")
            return True
        raise


def run(chain, market, epochs: int | None, submit_margin=0.9, auto_claim=True, lean=False):
    mined: set[int] = set()
    claimed: set[int] = set()   # track locally to avoid RPC-lag double-claims
    done = 0
    while epochs is None or done < epochs:
        e, seed, target = _rpc_call(chain.frontier)
        # Start nonces from a time-based offset so restarts never collide
        # with nonces already accepted this epoch (contract requires strictly
        # increasing nonces per miner per epoch).
        start_nonce = int(time.time()) * 10_000
        print(f"[epoch {e}] target=2^{target.bit_length()-1} — starting miner "
              f"({market.__class__.__name__}) nonce_start={start_nonce}")
        session = market.start_mining(seed, chain.addr, target,
                                      seconds=EPOCH_SECONDS * submit_margin,
                                      start_nonce=start_nonce)
        buf: list[int] = []
        submitted = 0
        try:
            while True:
                cur_epoch = _rpc_call(chain.current_epoch)
                if cur_epoch != e:
                    break
                buf.extend(session.poll())
                secs_left = _rpc_call(chain.seconds_to_flip)
                near_flip = secs_left < FLUSH_WINDOW
                status = "ok"
                while buf and (len(buf) >= SUBMIT_CHUNK or near_flip):
                    batch, buf = buf[:MAX_BATCH], buf[MAX_BATCH:]
                    status = _try_submit(chain, batch)
                    if status == "ok":
                        submitted += len(batch)
                        mined.add(e)
                        if lean:             # 1 share submitted — we're done
                            buf = []
                            break
                    else:                     # stale flip race / epoch full
                        buf = []
                        break
                if status != "ok" or (lean and submitted > 0):
                    break
                sleep_for = POLL_NEAR if near_flip else min(POLL_IDLE, max(secs_left - FLUSH_WINDOW - 5, POLL_NEAR))
                time.sleep(sleep_for)
        finally:
            session.stop()
        print(f"[epoch {e}] submitted {submitted} shares")

        if auto_claim:
            for ce in chain.claimable(mined):
                if ce in claimed:
                    continue
                if _try_claim(chain, ce):
                    claimed.add(ce)
                    time.sleep(CLAIM_SETTLE_SECS)  # let RPC state propagate
                    print(f"[claim  {ce}] balance now {ash(chain.balance())}")
        done += 1

    if auto_claim:
        # final sweep — pick up anything missed during the run
        for ce in chain.claimable(mined):
            if ce in claimed:
                continue
            if _try_claim(chain, ce):
                claimed.add(ce)
        if claimed:
            time.sleep(CLAIM_SETTLE_SECS)
    else:
        print(f"[no-claim mode] {len(mined)} epoch(s) mined — run 'ash.py claim' to sweep")
    print(f"final balance: {ash(chain.balance())}")


def _try_submit(chain, batch) -> str:
    """'ok' | 'stale' (epoch flipped mid-submit) | 'full' (SHARE_CAP hit)."""
    try:
        chain.submit(batch)
        print(f"  submitted batch of {len(batch)}")
        return "ok"
    except ChainRevert as ex:
        msg = str(ex)
        if "no work" in msg:
            print("  epoch flipped mid-submit — dropping stale shares")
            return "stale"
        if "epoch full" in msg:
            print("  epoch full (SHARE_CAP) — standing down this epoch")
            return "full"
        raise


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--epochs", type=int, default=None)
    p.add_argument("--market", default="mock",
                   choices=["mock", "lium"])
    p.add_argument("--gpu", default="A100", help="lium GPU model")
    p.add_argument("--rpc"), p.add_argument("--contract"), p.add_argument("--key")
    a = p.parse_args()

    if a.market == "mock":
        market = adapters.MockMarket()
        market.quote()
    else:
        market = adapters.LiumMarket(gpu=a.gpu)

    if a.dry_run:
        chain = SimChain()
    else:
        assert a.rpc and a.contract and a.key, "--rpc --contract --key required"
        chain = LiveChain(a.rpc, a.contract, a.key)

    run(chain, market, a.epochs)


if __name__ == "__main__":
    main()
