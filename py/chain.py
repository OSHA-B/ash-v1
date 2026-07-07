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
        self.w3 = Web3(Web3.HTTPProvider(rpc_url,
                         request_kwargs={"timeout": 15}))
        # Retry is_connected() — brief 429 on startup should not kill the process
        import time as _t
        for _i in range(5):
            if self.w3.is_connected():
                break
            _t.sleep(3 * (_i + 1))
        else:
            raise AssertionError("RPC unreachable after retries")
        # Inject chain_id so web3 validation middleware never fetches it
        # on every call — eliminates ~50% of RPC round-trips.
        try:
            self.w3.eth._chain_id = lambda: self.CHAIN_ID  # type: ignore
        except Exception:
            pass
        self.acct = self.w3.eth.account.from_key(privkey) if privkey else None
        self.c = self.w3.eth.contract(
            address=Web3.to_checksum_address(contract_addr),
            abi=json.load(open(abi_path)),
        )
        # Local nonce cache — avoids replacement-underpriced when batches
        # are submitted faster than the node's pending pool updates.
        self._nonce: int | None = None

    # ---- reads ----
    def frontier(self) -> tuple[int, bytes, int]:
        e, seed, target = self.c.functions.frontier().call()
        return e, bytes(seed), target

    def shares_of(self, epoch: int, addr: str) -> int:
        return self.c.functions.sharesOf(epoch, addr).call()

    def balance(self, addr: str) -> int:
        return self.c.functions.balanceOf(addr).call()

    # ---- writes ----
    # Bittensor EVM constants
    CHAIN_ID   = 964
    GAS_SUBMIT = 300_000   # covers submitShares(64 nonces)
    GAS_CLAIM  = 200_000   # covers claimMany([...] epochs)

    def _send(self, fn, gas: int):
        """Send with local nonce tracking and retry on nonce/rate errors."""
        import time as _time
        for attempt in range(4):
            if self._nonce is None or attempt > 0:
                self._nonce = self.w3.eth.get_transaction_count(
                    self.acct.address, 'pending')
            try:
                tx = fn.build_transaction({
                    "from":              self.acct.address,
                    "nonce":             self._nonce,
                    "chainId":           self.CHAIN_ID,
                    "gas":               gas,
                    # EIP-1559: base fee 10 gwei on Bittensor EVM, 2x headroom
                    "maxFeePerGas":      self.w3.to_wei("20", "gwei"),
                    "maxPriorityFeePerGas": self.w3.to_wei("1", "gwei"),
                })
                signed = self.acct.sign_transaction(tx)
                h = self.w3.eth.send_raw_transaction(signed.raw_transaction)
                self._nonce += 1
                return self.w3.eth.wait_for_transaction_receipt(h)
            except Exception as e:
                msg = str(e)
                if any(x in msg for x in
                       ('nonce too low', 'replacement transaction', 'already known',
                        '429', 'Too Many Requests')):
                    wait = 2 ** attempt
                    _time.sleep(wait)
                    self._nonce = None   # force re-fetch
                    continue
                raise

    def submit_shares(self, nonces: list[int]):
        """Submit ≤64 nonces mined against frontier(). If the epoch rolled
        between mining and inclusion, the tx reverts with 'ASH: no work' —
        just re-mine against the new frontier; nothing is lost but the gas."""
        assert 0 < len(nonces) <= 64
        return self._send(self.c.functions.submitShares(nonces), self.GAS_SUBMIT)

    def claim_many(self, epochs: list[int]):
        return self._send(self.c.functions.claimMany(epochs), self.GAS_CLAIM)
