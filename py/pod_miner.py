#!/usr/bin/env python3
"""
pod_miner.py — runs ON the rented GPU box. Zero dependencies, zero secrets.

Security model (why this is safe on untrusted hardware):
  The pod only ever sees (seed, address, target) — all public. Shares are
  address-bound: keccak(seed ‖ ADDRESS ‖ nonce), so the nonces this prints
  are worthless to the pod's host — they only pass verification when
  submitted FROM that address. The agent keeps the key and submits.
  Worst case for a malicious host: withhold nonces (you lose the rent).

Protocol (stdout, line-based — collected via lium exec streams):
  MINER start ...      once at boot
  NONCE <int>          every valid share found
  HASHRATE <float>     heartbeat every ~20k hashes
  MINER done ...       at deadline

Env:
  ASH_SEED    0x-hex bytes32 (from frontier())
  ASH_ADDR    0x-hex 20-byte miner address (msg.sender that will submit)
  ASH_TARGET  target as decimal or 0x-hex
  ASH_SECONDS mining duration (default 540 — one epoch minus margin)
  ASH_START_NONCE  optional starting nonce (default 1)

Production note: this CPU loop is the reference. A real deployment swaps
the while-loop for a GPU keccak grinder over the same 84-byte preimage
seed(32) ‖ addr(20) ‖ nonce(32, big-endian) — the stdout protocol and
everything around it stays identical.
"""
import os, sys, time

# ---- keccak-256: pycryptodome if present, else embedded pure-python -------
try:
    from Crypto.Hash import keccak as _k
    def keccak256(b: bytes) -> bytes:
        h = _k.new(digest_bits=256); h.update(b); return h.digest()
    BACKEND = "pycryptodome"
except ImportError:
    # verified byte-exact vs pycryptodome incl. padding boundaries (repo tests)
    _RC = [0x1,0x8082,0x800000000000808A,0x8000000080008000,0x808B,0x80000001,
     0x8000000080008081,0x8000000000008009,0x8A,0x88,0x80008009,0x8000000A,
     0x8000808B,0x800000000000008B,0x8000000000008089,0x8000000000008003,
     0x8000000000008002,0x8000000000000080,0x800A,0x800000008000000A,
     0x8000000080008081,0x8000000000008080,0x80000001,0x8000000080008008]
    _M = (1 << 64) - 1
    _R = [0,1,62,28,27,36,44,6,55,20,3,10,43,25,39,41,45,15,21,8,18,2,61,56,14]
    def _rol(v, n): return ((v << n) | (v >> (64 - n))) & _M
    def _f(st):
        for rc in _RC:
            c = [st[x]^st[x+5]^st[x+10]^st[x+15]^st[x+20] for x in range(5)]
            d = [c[(x-1)%5] ^ _rol(c[(x+1)%5], 1) for x in range(5)]
            st = [st[i] ^ d[i%5] for i in range(25)]
            b = [0]*25
            for x in range(5):
                for y in range(5):
                    b[y+5*((2*x+3*y)%5)] = _rol(st[x+5*y], _R[x+5*y])
            st = [b[i] ^ ((~b[(i//5)*5+((i%5)+1)%5]) & b[(i//5)*5+((i%5)+2)%5])
                  for i in range(25)]
            st[0] ^= rc
        return st
    def keccak256(data: bytes) -> bytes:
        rate = 136
        if (len(data)+1) % rate == 0:
            p = data + b"\x81"
        else:
            p = data + b"\x01" + b"\x00"*((-len(data)-2) % rate) + b"\x80"
        st = [0]*25
        for off in range(0, len(p), rate):
            blk = p[off:off+rate]
            for i in range(rate//8):
                st[i] ^= int.from_bytes(blk[i*8:(i+1)*8], "little")
            st = _f(st)
        return b"".join(st[i].to_bytes(8, "little") for i in range(4))
    BACKEND = "pure-python"


def main():
    seed = bytes.fromhex(os.environ["ASH_SEED"].replace("0x", "", 1))
    addr = bytes.fromhex(os.environ["ASH_ADDR"].replace("0x", "", 1))
    target = int(os.environ["ASH_TARGET"], 0)
    seconds = float(os.environ.get("ASH_SECONDS", "540"))
    nonce = int(os.environ.get("ASH_START_NONCE", "1"))
    assert len(seed) == 32 and len(addr) == 20

    prefix = seed + addr
    deadline = time.time() + seconds
    t0, tried = time.time(), 0
    print(f"MINER start backend={BACKEND} target=2^{target.bit_length()-1} "
          f"seconds={seconds:.0f}", flush=True)
    while time.time() < deadline:
        h = int.from_bytes(keccak256(prefix + nonce.to_bytes(32, "big")), "big")
        tried += 1
        if h < target:
            print(f"NONCE {nonce}", flush=True)
        if tried % 20000 == 0:
            print(f"HASHRATE {tried/(time.time()-t0):.0f}", flush=True)
        nonce += 1
    print(f"MINER done tried={tried} rate={tried/max(time.time()-t0,1e-9):.0f}",
          flush=True)


if __name__ == "__main__":
    main()
