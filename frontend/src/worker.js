/* worker.js — the in-page burner. Runs inside a Blob worker with ashlib
 * prepended. Grinds keccak(seed ‖ addr ‖ nonce) < target in small chunks
 * and posts found nonces. Demo-grade by design: real hashrate lives in
 * the pod/GPU miner; this exists so the page can burn for real. */
"use strict";
let running = false, seed = null, addr = null, target = 0n, nonce = 1n;
const CHUNK = 400;

self.onmessage = (ev) => {
  const m = ev.data;
  if (m.cmd === "start" || m.cmd === "retune") {
    seed = ASHLIB.hexToBytes(m.seed);
    addr = ASHLIB.hexToBytes(m.addr);
    target = BigInt(m.target);
    nonce = BigInt(m.startNonce || 1);
    if (m.cmd === "start" && !running) { running = true; tick(); }
  } else if (m.cmd === "stop") {
    running = false;
  }
};

function tick() {
  if (!running) return;
  const t0 = Date.now();
  const found = [];
  const pre = ASHLIB.concat(seed, addr);
  for (let i = 0; i < CHUNK; i++) {
    const h = ASHLIB.bytesToBig(
      ASHLIB.keccak256(ASHLIB.concat(pre, ASHLIB.be32(nonce))));
    if (h < target) found.push(nonce.toString());
    nonce++;
  }
  postMessage({ found, tried: CHUNK, ms: Date.now() - t0 });
  setTimeout(tick, 0);
}
