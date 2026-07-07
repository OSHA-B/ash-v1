"""
watcher.py — epoch/supply monitor → Telegram. Matches the curl-to-Telegram
ops pattern: no LLM in the loop, ~zero cost. UNTESTED live.

  TG_BOT_TOKEN=… TG_CHAT_ID=… python3 watcher.py --rpc … --contract 0x…
"""
import argparse, json, os, time, urllib.request

def tg(msg: str):
    tok, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not tok:
        print("[watch]", msg); return
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{tok}/sendMessage",
        data=json.dumps({"chat_id": chat, "text": msg}).encode(),
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rpc", required=True); p.add_argument("--contract", required=True)
    p.add_argument("--interval", type=int, default=60)
    a = p.parse_args()
    from chain import AshChain
    c = AshChain(a.rpc, a.contract)
    last_e, last_supply = -1, -1
    tg(f"ASH watcher up — {a.contract[:10]}…")
    while True:
        try:
            e, _, target = c.frontier()
            supply = c.c.functions.totalSupply().call()
            if e != last_e:
                tg(f"🔥 epoch {e} | target 2^{target.bit_length()-1} | supply {supply/1e18:,.2f} ASH")
                last_e = e
            elif supply != last_supply:
                tg(f"claimed → supply {supply/1e18:,.2f} ASH")
            last_supply = supply
        except Exception as ex:
            tg(f"⚠️ watcher error: {ex}")
        time.sleep(a.interval)

if __name__ == "__main__":
    main()
