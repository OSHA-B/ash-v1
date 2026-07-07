"""
adapters.py — GPU rental market adapters for ASH mining.

Design: the pod NEVER holds a key. It receives (seed, address, target) —
all public — and emits address-bound nonces on stdout/logs. The agent
holds the key and submits. A malicious host can only withhold nonces.

Markets:
  LiumMarket   — SN51 GPU rental via official `lium` CLI (lium.io v0.0.24+).
                 Prereq: lium init + lium topup. Supports A30→H100.
  MockMarket   — local CPU burn, no API keys needed.
"""

from __future__ import annotations
import json
import os
import random
import shutil
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

POD_MINER = Path(__file__).with_name("pod_miner.py")


@dataclass
class Quote:
    market: str
    gpu: str
    usd_per_hour: float
    est_hashrate: float | None   # H/s on keccak; None = calibrate on first run


class MiningSession:
    """A running miner somewhere. poll() returns new unique nonces."""
    cost_usd_per_hour: float = 0.0

    def poll(self) -> list[int]: ...
    def stop(self) -> None: ...


class GPUMarket:
    def quote(self) -> Quote: ...
    def start_mining(self, seed: bytes, addr: bytes, target: int,
                     seconds: float) -> MiningSession: ...


def _env(seed: bytes, addr: bytes, target: int, seconds: float,
         start_nonce: int = 1) -> dict[str, str]:
    return {
        "ASH_SEED": "0x" + seed.hex(),
        "ASH_ADDR": "0x" + addr.hex(),
        "ASH_TARGET": hex(target),
        "ASH_SECONDS": str(int(seconds)),
        "ASH_START_NONCE": str(start_nonce),
    }


def _parse_nonces(text: str, seen: set[int]) -> list[int]:
    out = []
    for line in text.splitlines():
        if line.startswith("NONCE "):
            try:
                n = int(line.split()[1])
            except (IndexError, ValueError):
                continue
            if n not in seen:
                seen.add(n)
                out.append(n)
    return out


# ============================================================ Mock (offline)
class MockSession(MiningSession):
    """Mines locally in-process — used by live_loop --dry-run and tests."""
    def __init__(self, seed, addr, target, hashes_per_poll=8000, price=0.30,
                 start_nonce=1):
        from miner import mine_shares
        self._mine, self.seed, self.addr, self.target = mine_shares, seed, addr, target
        self.budget = hashes_per_poll
        self.next_nonce = start_nonce
        self.cost_usd_per_hour = price
        self.stopped = False

    def poll(self) -> list[int]:
        if self.stopped:
            return []
        found, tried = self._mine(self.seed, self.addr, self.target,
                                  want=10**9, start_nonce=self.next_nonce,
                                  max_hashes=self.budget)
        self.next_nonce += tried
        return found

    def stop(self):
        self.stopped = True


class MockMarket(GPUMarket):
    """Oscillating spot price around a base — stands in for a real market."""
    def __init__(self, name="mock-4090", base_usd=0.34, hashrate=2.0e9, seed=7):
        self.name, self.base, self.hashrate = name, base_usd, hashrate
        self.rng = random.Random(seed)
        self.pods = 0
        self._last_price = base_usd

    def quote(self) -> Quote:
        self._last_price = round(self.base * (0.65 + 0.7 * self.rng.random()), 4)
        return Quote(self.name, "RTX-4090", self._last_price, self.hashrate)

    # legacy demo API (agent_demo.py)
    def rent(self, hours: float) -> str:
        self.pods += 1
        return f"pod-{self.name}-{self.pods}"

    def release(self, pod_id: str) -> None:
        pass

    def start_mining(self, seed, addr, target, seconds, start_nonce=1) -> MiningSession:
        return MockSession(seed, addr, target, price=self._last_price,
                          start_nonce=start_nonce)



# ============================================================ Lium (SN51)
class LiumSession(MiningSession):
    """Docker-run style session: lium up --image streams stdout directly."""
    def __init__(self, name: str, proc: subprocess.Popen, cost: float):
        self.name, self.proc = name, proc
        self.cost_usd_per_hour = cost
        self._seen: set[int] = set()
        self._lines: list[str] = []
        self._t = threading.Thread(target=self._pump, daemon=True)
        self._t.start()

    def _pump(self):
        for line in self.proc.stdout:            # type: ignore[union-attr]
            self._lines.append(line)

    def poll(self) -> list[int]:
        buf, self._lines = self._lines, []
        return _parse_nonces("".join(buf), self._seen)

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
        # best-effort cleanup — pod may have already exited
        subprocess.run(["lium", "rm", self.name], capture_output=True, timeout=120)


class LiumMarket(GPUMarket):
    """SN51 Lium via the official CLI (lium.io v0.0.24+).

    Prereq: pip install lium.io && lium init (one-time: API key + SSH keys)
            lium topup (add TAO balance for compute charges)
    Flow: lium up --gpu X --image python:3.11 --cmd '…' --ttl Nh -e K=V …
          → pod streams NONCE lines on stdout → lium rm on stop.
    Uses Docker-run mode (no SSH or scp needed — cleaner and portable).
    """
    EST_HASHRATE: dict[str, float | None] = {}

    def __init__(self, gpu: str = "A100"):
        self.gpu = gpu
        if not shutil.which("lium"):
            raise RuntimeError("lium CLI not found: pip3.11 install lium.io && lium init")

    def quote(self) -> Quote:
        """Price from `lium ls --format json`."""
        price = 0.0
        try:
            out = subprocess.run(
                ["lium", "ls", "--gpu", self.gpu, "--format", "json"],
                capture_output=True, text=True, timeout=30
            ).stdout
            nodes = json.loads(out) if out.strip() else []
            if not isinstance(nodes, list):
                nodes = nodes.get("nodes") or nodes.get("items") or []
            cands = []
            for n in nodes:
                for k in ("price_gpu", "price_total", "price_per_gpu_hour",
                          "usd_per_hour", "price"):
                    v = n.get(k)
                    if isinstance(v, (int, float)) and v > 0:
                        cands.append(float(v)); break
            if cands:
                price = min(cands)
        except Exception:
            pass
        return Quote("lium", self.gpu, price, self.EST_HASHRATE.get(self.gpu))

    def start_mining(self, seed, addr, target, seconds, start_nonce=1) -> MiningSession:
        name = f"ash-{random.randrange(16**6):06x}"
        hours = max(1, int(seconds // 3600) + 1)
        src = POD_MINER.read_text()
        envs = _env(seed, addr, target, seconds, start_nonce)
        # build env flag list: -e K=V -e K=V …
        env_args: list[str] = []
        for k, v in envs.items():
            env_args += ["-e", f"{k}={v}"]
        # Docker-run mode: --image + --cmd streams stdout directly.
        # We inline the miner source via -c to avoid scp.
        cmd = ["lium", "up",
               "--gpu", self.gpu,
               "--name", name,
               "--image", "python:3.11-slim",
               "--cmd", f"python3 -c {json.dumps(src)}",
               "--ttl", f"{hours}h",
               ] + env_args
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        q = self.quote()
        return LiumSession(name, proc, q.usd_per_hour)


# legacy alias
LiumAdapter = LiumMarket
