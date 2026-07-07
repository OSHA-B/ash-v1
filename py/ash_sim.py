"""
ash_sim.py — exact Python mirror of contracts/ASH.sol.

Every rule here is a line-for-line port of the Solidity: same retarget,
same seed chaining, same preimage layout, same pro-rata claim math.
Used to test the whole system end-to-end offline (miner + agent + epochs)
before anything touches a chain.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from Crypto.Hash import keccak

U256 = (1 << 256) - 1

# ---- constants (must match ASH.sol) ----------------------------------------
EPOCH_SECONDS  = 600
INITIAL_POOL   = 50 * 10**18
HALVING_EPOCHS = 210_000
CAP            = 21_000_000 * 10**18
TARGET_SHARES  = 512
SHARE_CAP      = TARGET_SHARES * 16
MAX_BATCH      = 64
MAX_EASING     = 64
MAX_TARGET     = U256 >> 8
MIN_TARGET     = 1 << 32


def keccak256(data: bytes) -> bytes:
    k = keccak.new(digest_bits=256)
    k.update(data)
    return k.digest()


def encode_packed_share(seed: bytes, addr: bytes, nonce: int) -> bytes:
    """abi.encodePacked(bytes32 seed, address sender, uint256 nonce)"""
    assert len(seed) == 32 and len(addr) == 20
    return seed + addr + nonce.to_bytes(32, "big")


def share_hash(seed: bytes, addr: bytes, nonce: int) -> int:
    return int.from_bytes(keccak256(encode_packed_share(seed, addr, nonce)), "big")


def pool_of(e: int) -> int:
    era = e // HALVING_EPOCHS
    return 0 if era >= 64 else INITIAL_POOL >> era


def next_params(prev_seed: bytes, prev_target: int, prev_epoch: int,
                prev_shares: int, now_epoch: int) -> tuple[bytes, int]:
    """Port of ASH._nextParams — the bang-bang retarget + reseed."""
    target = prev_target

    if prev_shares > TARGET_SHARES * 2:
        target >>= 1
    elif prev_shares > 0 and prev_shares * 2 < TARGET_SHARES:
        target = MAX_TARGET if target > (MAX_TARGET >> 1) else target << 1

    gap = now_epoch - prev_epoch
    empties = gap if prev_shares == 0 else gap - 1
    if empties > 0:
        sh = min(empties, MAX_EASING)
        target = MAX_TARGET if target > (MAX_TARGET >> sh) else target << sh

    target = max(MIN_TARGET, min(MAX_TARGET, target))

    # abi.encodePacked(bytes32, uint64, uint64, uint256, uint64)
    seed = keccak256(
        prev_seed
        + prev_epoch.to_bytes(8, "big")
        + prev_shares.to_bytes(8, "big")
        + target.to_bytes(32, "big")
        + now_epoch.to_bytes(8, "big")
    )
    return seed, target


@dataclass
class AshSim:
    """The contract, with block.timestamp replaced by a controllable clock."""
    initial_target: int = U256 >> 20
    now: int = 1_000_000                      # fake block.timestamp
    genesis: int = field(init=False)

    def __post_init__(self):
        assert MIN_TARGET <= self.initial_target <= MAX_TARGET
        self.genesis = self.now
        self.last_rolled = 0
        self.seed = keccak256(b"ASH/genesis-sim")
        self.target = self.initial_target
        self.total_shares: dict[int, int] = {}
        self.shares_of: dict[tuple[int, bytes], int] = {}
        self.last_nonce: dict[tuple[int, bytes], int] = {}
        self.claimed: set[tuple[int, bytes]] = set()
        self.total_supply = 0
        self.balance: dict[bytes, int] = {}
        self.events: list[str] = []

    # ---- clock ----
    def warp(self, seconds: int):
        self.now += seconds

    def current_epoch(self) -> int:
        return (self.now - self.genesis) // EPOCH_SECONDS

    # ---- epochs ----
    def frontier(self) -> tuple[int, bytes, int]:
        e = self.current_epoch()
        if e == self.last_rolled:
            return e, self.seed, self.target
        seed, target = next_params(
            self.seed, self.target, self.last_rolled,
            self.total_shares.get(self.last_rolled, 0), e
        )
        return e, seed, target

    def roll(self):
        e = self.current_epoch()
        prev = self.last_rolled
        if e == prev:
            return
        s = self.total_shares.get(prev, 0)
        self.seed, self.target = next_params(self.seed, self.target, prev, s, e)
        self.last_rolled = e
        self.events.append(f"Rolled(epoch={e}, target=2^{self.target.bit_length()-1}, prevShares={s})")

    # ---- mining ----
    def submit_shares(self, sender: bytes, nonces: list[int]):
        self.roll()
        e = self.last_rolled
        n = len(nonces)
        if not (0 < n <= MAX_BATCH):
            raise ValueError("ASH: batch size")
        last = self.last_nonce.get((e, sender), 0)
        for x in nonces:
            if x <= last:
                raise ValueError("ASH: nonce order")
            if share_hash(self.seed, sender, x) >= self.target:
                raise ValueError("ASH: no work")
            last = x
        self.last_nonce[(e, sender)] = last
        if self.total_shares.get(e, 0) + n > SHARE_CAP:
            raise ValueError("ASH: epoch full")
        self.shares_of[(e, sender)] = self.shares_of.get((e, sender), 0) + n
        self.total_shares[e] = self.total_shares.get(e, 0) + n

    # ---- claims ----
    def claim(self, sender: bytes, e: int) -> int:
        if e >= self.current_epoch():
            raise ValueError("ASH: epoch live")
        if (e, sender) in self.claimed:
            raise ValueError("ASH: claimed")
        mine = self.shares_of.get((e, sender), 0)
        if mine == 0:
            raise ValueError("ASH: no shares")
        self.claimed.add((e, sender))
        amount = pool_of(e) * mine // self.total_shares[e]
        if self.total_supply + amount > CAP:
            raise ValueError("ASH: cap")
        self.total_supply += amount
        self.balance[sender] = self.balance.get(sender, 0) + amount
        return amount


def ash(n_wei: int) -> str:
    return f"{n_wei / 1e18:,.4f} ASH"
