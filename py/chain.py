"""
chain.py — thin client for a real ASH deployment.  ** UNTESTED SCAFFOLD **

Wire-up for when the contract is actually deployed. Nothing here has run
against a live chain. Verify the RPC URL and chain id from the official
docs of your target chain (Bittensor EVM or Base), deploy to a TESTNET
first, and confirm one mined share round-trips before anything real.

    pip install web3
"""

from __future__ import annotations
import json

try:
    from web3 import Web3
except ImportError:              # keep offline demo importable
    Web3 = None


class AshChain:
    def __init__(self, rpc_url: str, contract_addr: str,
                 abi_path: str = "out_ASH.abi.json", privkey: str | None = None):
        if Web3 is None:
            raise RuntimeError("pip install web3")
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        assert self.w3.is_connected(), "RPC unreachable"
        self.acct = self.w3.eth.account.from_key(privkey) if privkey else None
        self.c = self.w3.eth.contract(
            address=Web3.to_checksum_address(contract_addr),
            abi=json.load(open(abi_path)),
        )

    # ---- reads ----
    def frontier(self) -> tuple[int, bytes, int]:
        e, seed, target = self.c.functions.frontier().call()
        return e, bytes(seed), target

    def shares_of(self, epoch: int, addr: str) -> int:
        return self.c.functions.sharesOf(epoch, addr).call()

    def balance(self, addr: str) -> int:
        return self.c.functions.balanceOf(addr).call()

    # ---- writes ----
    def _send(self, fn):
        tx = fn.build_transaction({
            "from": self.acct.address,
            "nonce": self.w3.eth.get_transaction_count(self.acct.address),
            # gas / fee fields: set per target chain's fee model
        })
        signed = self.acct.sign_transaction(tx)
        h = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return self.w3.eth.wait_for_transaction_receipt(h)

    def submit_shares(self, nonces: list[int]):
        """Submit ≤64 nonces mined against frontier(). If the epoch rolled
        between mining and inclusion, the tx reverts with 'ASH: no work' —
        just re-mine against the new frontier; nothing is lost but the gas."""
        assert 0 < len(nonces) <= 64
        return self._send(self.c.functions.submitShares(nonces))

    def claim_many(self, epochs: list[int]):
        return self._send(self.c.functions.claimMany(epochs))
