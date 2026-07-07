#!/usr/bin/env python3
"""
mcp_server.py — expose ASH as MCP tools for any agent (Claude, OpenClaw…).

Zero-dependency stdio MCP server. Each tool shells out to the tested
ash.py CLI and returns its JSON, so the agent gets a clean, structured
surface:

  ash_identity      -> the EVM address it mines with
  ash_fund_address  -> SS58 mirror + exact btcli command to fund gas
  ash_status        -> chain/epoch/balance/gas snapshot
  ash_burn          -> burn compute for N epochs (market: local|lium)
  ash_claim         -> sweep all claimable epochs into ASH
  ash_withdraw      -> send gas TAO back to an SS58 coldkey

Register in an MCP client (e.g. Claude Desktop / OpenClaw config):
  {
    "mcpServers": {
      "ash": { "command": "python3", "args": ["/ABS/PATH/py/mcp_server.py"] }
    }
  }

Long-running burns: ash_burn accepts a bounded `epochs` so a tool call
returns; loop it, or run `python3 ash.py burn` directly for an unbounded
miner under a process manager.
"""
import json, subprocess, sys, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLI = str(HERE / "ash.py")

TOOLS = [
    {"name": "ash_provision",
     "description": "Create all wallets in one shot: a Bittensor coldkey (receives TAO for compute + gas), an ICP identity + account (receives ICP for hosting cycles), and the EVM mining key. Run this first. Requires btcli and dfx installed.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ash_wallets",
     "description": "Print the two fundable addresses (Bittensor SS58 + ICP account) in a clean block. Returns 'fundable_message' — send that to the user as its own separate message so they can fund both wallets in two clicks.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ash_identity",
     "description": "Show the local EVM address this agent mines ASH with (creates the key on first use).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ash_fund_address",
     "description": "Get the SS58 'mirror' address to send TAO to (via btcli) so the mining EVM address has gas. Returns the exact command.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ash_status",
     "description": "Snapshot: RPC reachable, current epoch, seconds left, difficulty, epoch shares, total supply, this agent's ASH balance and TAO gas.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ash_burn",
     "description": "Burn compute into ASH for a bounded number of epochs. market 'local' burns own hardware with no API keys; 'lium' rents a GPU from SN51 (needs: lium init + lium topup). Mines, submits shares, and claims as epochs finish.",
     "inputSchema": {"type": "object", "properties": {
         "market": {"type": "string", "enum": ["local", "lium"], "default": "local"},
         "epochs": {"type": "integer", "description": "how many epochs to mine before returning", "default": 2}}}},
    {"name": "ash_claim",
     "description": "Sweep every claimable finished epoch into ASH in one transaction.",
     "inputSchema": {"type": "object", "properties": {
         "lookback": {"type": "integer", "default": 64}}}},
    {"name": "ash_withdraw",
     "description": "Send EVM gas TAO back to an SS58 coldkey via the withdraw precompile.",
     "inputSchema": {"type": "object", "properties": {
         "to": {"type": "string"}, "amount": {"type": "string"}},
         "required": ["to", "amount"]}},
]

def run_cli(args: list[str]) -> str:
    try:
        out = subprocess.run([sys.executable, CLI, *args], capture_output=True,
                             text=True, timeout=None)
        return out.stdout.strip() or out.stderr.strip() or "{}"
    except Exception as e:
        return json.dumps({"error": str(e)})

def dispatch(name: str, args: dict) -> str:
    if name == "ash_provision":    return run_cli(["provision"])
    if name == "ash_wallets":      return run_cli(["wallets"])
    if name == "ash_identity":     return run_cli(["identity"])
    if name == "ash_fund_address": return run_cli(["fund-address"])
    if name == "ash_status":       return run_cli(["status"])
    if name == "ash_burn":
        return run_cli(["burn", "--market", args.get("market", "local"),
                        "--epochs", str(args.get("epochs", 2))])
    if name == "ash_claim":
        return run_cli(["claim", "--lookback", str(args.get("lookback", 64))])
    if name == "ash_withdraw":
        return run_cli(["withdraw", "--to", args["to"], "--amount", str(args["amount"])])
    return json.dumps({"error": f"unknown tool {name}"})

def send(obj): sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        try: req = json.loads(line)
        except json.JSONDecodeError: continue
        mid, method, params = req.get("id"), req.get("method"), req.get("params", {})
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ash", "version": "1.0.0"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = params.get("name"); args = params.get("arguments", {})
            text = dispatch(name, args)
            send({"jsonrpc": "2.0", "id": mid,
                  "result": {"content": [{"type": "text", "text": text}]}})
        elif method in ("notifications/initialized", "notifications/cancelled"):
            continue
        else:
            if mid is not None:
                send({"jsonrpc": "2.0", "id": mid,
                      "error": {"code": -32601, "message": f"method {method} not found"}})

if __name__ == "__main__":
    main()
