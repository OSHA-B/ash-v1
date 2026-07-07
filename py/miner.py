"""
miner.py — reference ASH miner.

Grinds keccak256(seed ‖ address ‖ nonce) < target, exactly the contract's
preimage. This CPU version is the correctness reference and the fallback
that keeps the chain alive at eased difficulty; production mining is the
same loop as a GPU keccak kernel (the puzzle is a stock keccak grind —
any existing GPU keccak miner can be pointed at it by swapping the
preimage packing).

Usage as a library:
    found, hashes = mine_shares(seed, addr, target, want=4, start_nonce=1)
"""

from __future__ import annotations
import time
from Crypto.Hash import keccak


def _keccak(data: bytes) -> bytes:
    k = keccak.new(digest_bits=256)
    k.update(data)
    return k.digest()


def mine_shares(seed: bytes, addr: bytes, target: int, want: int,
                start_nonce: int = 1, max_hashes: int = 50_000_000
                ) -> tuple[list[int], int]:
    """Grind until `want` valid nonces found. Returns (nonces, hashes_tried)."""
    prefix = seed + addr                       # constant 52-byte head
    found: list[int] = []
    nonce = start_nonce
    tried = 0
    while len(found) < want and tried < max_hashes:
        h = int.from_bytes(_keccak(prefix + nonce.to_bytes(32, "big")), "big")
        tried += 1
        if h < target:
            found.append(nonce)
        nonce += 1
    return found, tried


def bench(seconds: float = 1.0) -> float:
    """Local hashrate in H/s — used by the agent for EV estimates."""
    seed = b"\x11" * 32
    addr = b"\x22" * 20
    prefix = seed + addr
    n, t0 = 0, time.time()
    while time.time() - t0 < seconds:
        _keccak(prefix + n.to_bytes(32, "big"))
        n += 1
    return n / (time.time() - t0)


if __name__ == "__main__":
    hr = bench(1.0)
    print(f"reference CPU hashrate: {hr:,.0f} H/s")
    target = (1 << 256) >> 14          # 1 share per ~16k hashes
    t0 = time.time()
    nonces, tried = mine_shares(b"\xAA" * 32, b"\xBB" * 20, target, want=3)
    dt = time.time() - t0
    print(f"found {len(nonces)} shares in {tried:,} hashes ({dt:.2f}s): {nonces}")
