#!/usr/bin/env python3
"""
ash.py — the ASH agent CLI. One command surface for an autonomous agent
with a Bittensor wallet to burn compute into ASH.

Everything an agent needs, nothing it doesn't:

  python3 ash.py provision                # make ALL wallets: Bittensor + ICP + EVM
  python3 ash.py wallets                   # print the two fundable addresses, cleanly
  python3 ash.py identity                 # make/show the EVM key it mines with
  python3 ash.py fund-address             # SS58 mirror to send TAO to (btcli)
  python3 ash.py status                   # chain reachable? epoch? balance? gas?
  python3 ash.py burn --market local      # burn OWN cpu/gpu — zero API keys
  python3 ash.py burn --market lium       # rent GPU from SN51 (lium init + lium topup)
  python3 ash.py claim                     # sweep every claimable epoch
  python3 ash.py withdraw --to <ss58> --amount <tao>   # gas back to coldkey

Config resolution (each overridable by flag):
  ASH_HOME       default ~/.ash           (stores evm_key.json)
  ASH_NETWORK    default bittensor-test    (bittensor | bittensor-test | base)
  ASH_RPC        default = network preset
  ASH_CONTRACT   the deployed ASH address (required for burn/claim/status)

Design: burn/claim reuse the tested py/live_loop.py engine unchanged — this
file only adds identity, the funding-address bridge, and a clean CLI. Keys
live in ASH_HOME with 0600 perms and never leave the machine; the pods it
rents receive only public (seed, address, target) and mine address-bound
nonces, so a hostile host can withhold work but never steal it.
"""
from __future__ import annotations
import argparse, hashlib, json, os, sys, time
from pathlib import Path

# ---------------------------------------------------------------- networks
NETS = {
    "bittensor":      dict(chain_id=964, rpc="https://lite.chain.opentensor.ai"),
    "bittensor-test": dict(chain_id=945, rpc="https://test.chain.opentensor.ai"),
    "base":           dict(chain_id=8453, rpc="https://mainnet.base.org"),
}

def cfg():
    home = Path(os.environ.get("ASH_HOME", str(Path.home() / ".ash")))
    home.mkdir(parents=True, exist_ok=True)
    net = os.environ.get("ASH_NETWORK", "bittensor-test")
    preset = NETS.get(net, NETS["bittensor-test"])
    rpc = os.environ.get("ASH_RPC", preset["rpc"])
    contract = os.environ.get("ASH_CONTRACT", "").strip()
    return home, net, rpc, contract, preset["chain_id"]

# ---------------------------------------------------------------- SS58 bridge
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
def _b58(b: bytes) -> str:
    n = int.from_bytes(b, "big"); s = ""
    while n: n, r = divmod(n, 58); s = _B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s

def h160_to_ss58(h160: str, prefix: int = 42) -> str:
    """The 'Ethereum mirror' SS58: blake2(evm: ‖ address) → SS58(prefix 42).
    Verified against the canonical Substrate Alice vector. TAO sent here
    via btcli lands as EVM gas on the same H160 address."""
    addr = bytes.fromhex(h160.replace("0x", ""))
    assert len(addr) == 20, "address must be 20 bytes"
    pub = hashlib.blake2b(b"evm:" + addr, digest_size=32).digest()
    data = bytes([prefix]) + pub
    chk = hashlib.blake2b(b"SS58PRE" + data, digest_size=64).digest()[:2]
    return _b58(data + chk)

# ---------------------------------------------------------------- identity
def load_or_make_key(home: Path):
    """Create/load the local EVM key. Uses eth_account if available (proper
    secp256k1); prints an honest note if it must fall back."""
    kp = home / "evm_key.json"
    if kp.exists():
        d = json.loads(kp.read_text())
        return d["address"], d["private_key"]
    try:
        from eth_account import Account
        acct = Account.create()
        addr, pk = acct.address, acct.key.hex()
    except ImportError:
        print("! eth_account not installed — cannot safely generate a key.\n"
              "  pip install eth-account  (or import an existing key by writing\n"
              f"  {kp} as {{'address','private_key'}}).", file=sys.stderr)
        sys.exit(2)
    kp.write_text(json.dumps({"address": addr, "private_key": pk}, indent=2))
    os.chmod(kp, 0o600)
    return addr, pk

# ---------------------------------------------------------------- web3 helpers
def w3_and_contract(rpc, contract):
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(rpc))
    if not w3.is_connected():
        raise SystemExit(f"cannot reach RPC {rpc}")
    if not contract:
        raise SystemExit("set ASH_CONTRACT to the deployed ASH address")
    abi_path = Path(__file__).resolve().parent.parent / "out_ASH.abi.json"
    abi = json.loads(abi_path.read_text())
    c = w3.eth.contract(address=Web3.to_checksum_address(contract), abi=abi)
    return w3, c

# ---------------------------------------------------------------- Bittensor wallet
def provision_bittensor(home: Path, net: str):
    """Create (or detect) a Bittensor coldkey — the address that receives TAO.
    Records only public info in ASH_HOME; the mnemonic is emitted by btcli
    exactly once to the terminal and is the operator's to secure. Returns
    {name, ss58, created, mnemonic_shown}. Requires btcli on PATH."""
    import shutil, subprocess, re
    rec = home / "bittensor_wallet.json"
    if rec.exists():
        d = json.loads(rec.read_text()); d["created"] = False; return d
    if not shutil.which("btcli"):
        return {"error": "btcli not found — pip install bittensor-cli, then re-run provision",
                "name": None, "ss58": None, "created": False}
    name = os.environ.get("ASH_BT_WALLET", "ash_miner")
    # non-interactive coldkey creation; no password, 12-word mnemonic.
    # btcli prints the mnemonic ONCE — we surface a security note, never store it.
    try:
        subprocess.run(
            ["btcli", "wallet", "new_coldkey", "--wallet.name", name,
             "--n-words", "12", "--no-use-password"],
            check=True, timeout=120)
    except subprocess.CalledProcessError as e:
        return {"error": f"btcli coldkey creation failed: {e}", "name": name,
                "ss58": None, "created": False}
    # read back the public ss58 from the keyfile dir
    ss58 = None
    try:
        out = subprocess.run(["btcli", "wallet", "list"], capture_output=True,
                             text=True, timeout=60).stdout
        m = re.search(re.escape(name) + r"\s*\((5[1-9A-HJ-NP-Za-km-z]{47,48})\)", out)
        if m: ss58 = m.group(1)
    except Exception:
        pass
    d = {"name": name, "ss58": ss58, "network": net, "created": True,
         "mnemonic_shown": True}
    rec.write_text(json.dumps(d, indent=2)); os.chmod(rec, 0o600)
    return d

# ---------------------------------------------------------------- ICP identity
def provision_icp(home: Path):
    """Create (or detect) an ICP identity + its ledger account — the address
    that receives ICP for canister cycles. Returns {principal, account_id,
    created}. Requires dfx on PATH."""
    import shutil, subprocess
    rec = home / "icp_identity.json"
    if rec.exists():
        d = json.loads(rec.read_text()); d["created"] = False; return d
    if not shutil.which("dfx"):
        return {"error": "dfx not found — install the IC SDK, then re-run provision",
                "principal": None, "account_id": None, "created": False}
    name = os.environ.get("ASH_ICP_IDENTITY", "ash_deployer")
    created = True
    try:
        # create identity if absent (dfx errors if it exists — treat as detect)
        r = subprocess.run(["dfx", "identity", "new", name, "--storage-mode", "plaintext"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0 and "already exists" in (r.stderr + r.stdout):
            created = False
        principal = subprocess.run(
            ["dfx", "identity", "get-principal", "--identity", name],
            capture_output=True, text=True, timeout=60).stdout.strip()
        account_id = subprocess.run(
            ["dfx", "ledger", "account-id", "--identity", name],
            capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception as e:
        return {"error": f"dfx identity provisioning failed: {e}",
                "principal": None, "account_id": None, "created": False}
    d = {"identity": name, "principal": principal or None,
         "account_id": account_id or None, "created": created}
    rec.write_text(json.dumps(d, indent=2)); os.chmod(rec, 0o600)
    return d

# ---------------------------------------------------------------- commands
def cmd_provision(a):
    """Create all three wallets in one shot: Bittensor coldkey (TAO),
    ICP identity (cycles), and the EVM mining key (auto)."""
    home, net, rpc, contract, cid = cfg()
    evm_addr, _ = load_or_make_key(home)
    bt = provision_bittensor(home, net)
    icp = provision_icp(home)
    print(json.dumps({
        "evm_mining_address": evm_addr,
        "bittensor": bt,
        "icp": icp,
        "next": "run  python3 ash.py wallets  to print the two fundable "
                "addresses, then send that message to the operator.",
    }, indent=2))

def cmd_wallets(a):
    """Print the two addresses the operator funds — formatted so it can be
    pasted to the user as a standalone message and actioned in two clicks."""
    home, net, rpc, contract, cid = cfg()
    evm_addr, _ = load_or_make_key(home)
    # ensure both exist (idempotent)
    bt_rec = home / "bittensor_wallet.json"
    icp_rec = home / "icp_identity.json"
    bt = json.loads(bt_rec.read_text()) if bt_rec.exists() else provision_bittensor(home, net)
    icp = json.loads(icp_rec.read_text()) if icp_rec.exists() else provision_icp(home)
    evm_ss58 = h160_to_ss58(evm_addr)
    bt_net_flag = "" if net == "bittensor" else " --network test"

    block = []
    block.append("═══════════════════════════════════════════════════════")
    block.append("  ASH is provisioned. Fund these two wallets to begin.")
    block.append("═══════════════════════════════════════════════════════")
    block.append("")
    block.append("① BITTENSOR  — send TAO here (pays for compute + gas)")
    block.append(f"   Address (SS58):  {bt.get('ss58') or '[run provision]'}")
    block.append(f"   Fund with ~0.5 TAO:")
    block.append(f"     btcli wallet transfer --dest {bt.get('ss58') or '<SS58>'} --amount 0.5{bt_net_flag}")
    block.append("")
    block.append("② INTERNET COMPUTER  — send ICP here (pays for hosting/cycles)")
    block.append(f"   Account ID:  {icp.get('account_id') or '[run provision]'}")
    block.append(f"   Principal:   {icp.get('principal') or '[run provision]'}")
    block.append(f"   Fund with ~0.5 ICP from any exchange to the Account ID above.")
    block.append("")
    block.append("───────────────────────────────────────────────────────")
    block.append(f"   (mining EVM address, auto-gassed from ①: {evm_addr}")
    block.append(f"    via its TAO mirror {evm_ss58})")
    block.append("═══════════════════════════════════════════════════════")
    human = "\n".join(block)

    print(json.dumps({
        "fundable_message": human,
        "bittensor_ss58": bt.get("ss58"),
        "bittensor_fund_amount_tao": 0.5,
        "icp_account_id": icp.get("account_id"),
        "icp_principal": icp.get("principal"),
        "icp_fund_amount_icp": 0.5,
        "evm_mining_address": evm_addr,
        "evm_tao_mirror_ss58": evm_ss58,
        "instructions_for_agent":
            "Send the string in 'fundable_message' to the user as its OWN "
            "separate message, verbatim, once provisioning is complete. It "
            "contains the two addresses the user funds in two clicks.",
    }, indent=2))

def cmd_identity(a):
    home, net, rpc, contract, cid = cfg()
    addr, _ = load_or_make_key(home)
    print(json.dumps({"evm_address": addr, "network": net,
                      "chain_id": cid, "rpc": rpc,
                      "key_file": str(home / "evm_key.json")}, indent=2))

def cmd_fund_address(a):
    home, net, rpc, contract, cid = cfg()
    addr, _ = load_or_make_key(home)
    ss58 = h160_to_ss58(addr)
    print(json.dumps({
        "send_tao_to_ss58": ss58,
        "for_evm_address": addr,
        "network": net,
        "btcli_command":
            f"btcli wallet transfer --dest {ss58} --amount 0.5"
            + ("" if net == "bittensor" else "  # (point btcli at test network)"),
        "note": "TAO sent to this SS58 mirror becomes gas on the EVM address. "
                "Start with ~0.2-0.5 TAO for gas; rentals are paid via the "
                "market's own API/credits, not from this balance.",
    }, indent=2))

def cmd_status(a):
    home, net, rpc, contract, cid = cfg()
    addr, _ = load_or_make_key(home)
    out = {"evm_address": addr, "network": net, "rpc": rpc, "contract": contract or None}
    try:
        w3, c = w3_and_contract(rpc, contract)
        e = c.functions.currentEpoch().call()
        out.update(
            reachable=True,
            gas_tao=str(w3.from_wei(w3.eth.get_balance(addr), "ether")),
            current_epoch=e,
            epoch_seconds_left=c.functions.secondsToNextEpoch().call(),
            share_target_bits=c.functions.shareTarget().call().bit_length() - 1,
            epoch_shares=c.functions.totalShares(e).call(),
            total_supply_ash=str(w3.from_wei(c.functions.totalSupply().call(), "ether")),
            your_balance_ash=str(w3.from_wei(c.functions.balanceOf(addr).call(), "ether")),
        )
    except SystemExit as ex:
        out.update(reachable=False, error=str(ex))
    print(json.dumps(out, indent=2))

def cmd_burn(a):
    """Delegate to the tested live_loop engine with our identity + chain."""
    home, net, rpc, contract, cid = cfg()
    addr, pk = load_or_make_key(home)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import live_loop, adapters
    # choose market
    if a.market == "local":
        market = adapters.MockMarket()   # MockMarket mines in-process = local burn
        market.quote()
        print("burning on local hardware (MockMarket in-process miner). "
              "For real GPU throughput, wire a GPU keccak kernel — see AGENTS.md.")
    elif a.market == "lium":
        market = adapters.LiumMarket(gpu=a.gpu)
    else:
        raise SystemExit(f"unknown market {a.market}")
    chain = live_loop.LiveChain(rpc, contract, pk)
    live_loop.run(chain, market, epochs=a.epochs)

def cmd_claim(a):
    home, net, rpc, contract, cid = cfg()
    addr, pk = load_or_make_key(home)
    w3, c = w3_and_contract(rpc, contract)
    cur = c.functions.currentEpoch().call()
    lo = max(0, cur - a.lookback)
    claimable = []
    for e in range(lo, cur):
        if c.functions.sharesOf(e, addr).call() > 0 and not c.functions.claimed(e, addr).call():
            claimable.append(e)
    if not claimable:
        print(json.dumps({"claimable_epochs": 0, "message": "nothing to claim"})); return
    acct = w3.eth.account.from_key(pk)
    tx = c.functions.claimMany(claimable).build_transaction({
        "from": addr, "nonce": w3.eth.get_transaction_count(addr),
        "chainId": cid, "gas": 200000 + 60000 * len(claimable),
        "gasPrice": w3.eth.gas_price})
    signed = acct.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(h)
    print(json.dumps({"claimed_epochs": claimable, "tx": h.hex(),
                      "new_balance_ash": str(w3.from_wei(c.functions.balanceOf(addr).call(), "ether"))}, indent=2))

def cmd_withdraw(a):
    """Send EVM gas TAO back to an SS58 coldkey via the withdraw precompile."""
    home, net, rpc, contract, cid = cfg()
    addr, pk = load_or_make_key(home)
    from web3 import Web3
    try:
        from substrateinterface.utils.ss58 import ss58_decode
        pub = "0x" + ss58_decode(a.to)
    except ImportError:
        raise SystemExit("pip install substrate-interface to decode the SS58 destination")
    w3 = Web3(Web3.HTTPProvider(rpc))
    PRECOMPILE = Web3.to_checksum_address("0x0000000000000000000000000000000000000800")
    # withdraw(bytes32 pubkey, uint256 amount) — Subtensor EVM withdraw precompile
    selector = w3.keccak(text="withdraw(bytes32,uint256)")[:4]
    data = selector + bytes.fromhex(pub[2:]).rjust(32, b"\0") \
        + int(float(a.amount) * 1e18).to_bytes(32, "big")
    acct = w3.eth.account.from_key(pk)
    tx = {"from": addr, "to": PRECOMPILE, "data": data, "chainId": cid,
          "nonce": w3.eth.get_transaction_count(addr),
          "gas": 100000, "gasPrice": w3.eth.gas_price}
    signed = acct.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(h)
    print(json.dumps({"withdrew_tao": a.amount, "to_ss58": a.to, "tx": h.hex()}, indent=2))

# ---------------------------------------------------------------- argparse
def main():
    p = argparse.ArgumentParser(prog="ash", description="ASH agent CLI")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("provision")
    sub.add_parser("wallets")
    sub.add_parser("identity")
    sub.add_parser("fund-address")
    sub.add_parser("status")
    b = sub.add_parser("burn")
    b.add_argument("--market", default="local", choices=["local", "lium"])
    b.add_argument("--epochs", type=int, default=None)
    b.add_argument("--gpu", default="A100"); b.add_argument("--resource", default="h200-small")
    cl = sub.add_parser("claim"); cl.add_argument("--lookback", type=int, default=64)
    wd = sub.add_parser("withdraw")
    wd.add_argument("--to", required=True); wd.add_argument("--amount", required=True)
    a = p.parse_args()
    {"provision": cmd_provision, "wallets": cmd_wallets,
     "identity": cmd_identity, "fund-address": cmd_fund_address, "status": cmd_status,
     "burn": cmd_burn, "claim": cmd_claim, "withdraw": cmd_withdraw}[a.cmd](a)

if __name__ == "__main__":
    main()
