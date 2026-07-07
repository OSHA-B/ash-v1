/* app.js — ASH furnace panel logic. No frameworks, no dependencies. */
"use strict";
const A = ASHLIB;

// ------------------------------------------------------------ networks
const NETS = {
  "bittensor": { label: "Bittensor EVM", chainId: "0x3c4",
    rpc: "https://lite.chain.opentensor.ai",
    add: { chainName: "Bittensor EVM", nativeCurrency: { name: "TAO", symbol: "TAO", decimals: 18 },
           rpcUrls: ["https://lite.chain.opentensor.ai"] } },
  "bittensor-test": { label: "Bittensor testnet", chainId: "0x3b1",
    rpc: "https://test.chain.opentensor.ai",
    add: { chainName: "Bittensor EVM Testnet", nativeCurrency: { name: "TAO", symbol: "TAO", decimals: 18 },
           rpcUrls: ["https://test.chain.opentensor.ai"] } },
  "base": { label: "Base", chainId: "0x2105", rpc: "https://mainnet.base.org",
    add: { chainName: "Base", nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
           rpcUrls: ["https://mainnet.base.org"] } },
  "custom": { label: "Custom", chainId: null, rpc: "", add: null },
};

// ------------------------------------------------------------ state
const ASH_CONTRACT = "0xA0EadE44e10C433E253aADd073cdFEd6af97F43A";
const S = {
  net: "bittensor", rpc: NETS["bittensor"].rpc, contract: ASH_CONTRACT,
  account: null, chainOk: false, watchAddr: null,
  epoch: null, seed: null, target: null, epochShares: 0n,
  supply: 0n, cap: 21000000n * 10n ** 18n, pool: 0n,
  targetShares: 512n, shareCap: 8192n, epochSeconds: 600,
  flipDeadline: 0, constantsLoaded: false,
  mining: false, worker: null, hashrate: 0, tried: 0,
  buf: [], submitting: false, autoSubmit: true,
  claimRows: [], txPending: false,
};

const $ = (id) => document.getElementById(id);
const short = (a) => a ? a.slice(0, 6) + "…" + a.slice(-4) : "—";
const fmtAsh = (wei, dp = 4) => {
  const s = (Number(wei) / 1e18);
  return s.toLocaleString(undefined, { maximumFractionDigits: dp });
};
const bits = (t) => t ? (t.toString(2).length - 1) : 0;
const now = () => Date.now();

function log(msg, cls) {
  const el = document.createElement("div");
  el.className = "logline" + (cls ? " " + cls : "");
  el.textContent = `${new Date().toTimeString().slice(0, 8)}  ${msg}`;
  const box = $("log");
  box.prepend(el);
  while (box.children.length > 80) box.removeChild(box.lastChild);
}

// ------------------------------------------------------------ config
function readConfig() {
  const q = new URLSearchParams(location.search);
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("ash-cfg") || "{}"); } catch (_) {}
  // URL params win, then localStorage, then baked-in defaults
  S.net = q.get("net") || saved.net || S.net;
  const preset = NETS[S.net] || NETS["custom"];
  S.rpc = q.get("rpc") || saved.rpc || preset.rpc || NETS["bittensor"].rpc;
  S.contract = (q.get("contract") || saved.contract || ASH_CONTRACT).trim();
  const w = (q.get("watch") || "").trim();
  S.watchAddr = w && w.startsWith("0x") ? w : null;
}
function writeConfig() {
  try { localStorage.setItem("ash-cfg",
    JSON.stringify({ net: S.net, rpc: S.rpc, contract: S.contract })); } catch (_) {}
  const q = new URLSearchParams();
  q.set("net", S.net);
  if (S.rpc !== (NETS[S.net] || {}).rpc) q.set("rpc", S.rpc);
  if (S.contract) q.set("contract", S.contract);
  history.replaceState(null, "", "?" + q.toString());
}
function applyConfigUI() {
  $("net-select").value = S.net;
  $("rpc-input").value = S.rpc;
  $("contract-input").value = S.contract;
  $("contract-pill").textContent = S.contract ? short(S.contract) : "no contract";
  const unset = !S.contract || !S.rpc;
  if (unset) $("config").open = true;   // only force-open when actually misconfigured
  $("hint").hidden = !unset;
}

// ------------------------------------------------------------ polling
async function loadConstants() {
  const c = A.calldata;
  const [gen, es, cap, ts, sc] = await A.ethCallBatch(S.rpc, S.contract, [
    c.genesis(), c.EPOCH_SECONDS(), c.CAP(), c.TARGET_SHARES(), c.SHARE_CAP()]);
  S.epochSeconds = Number(A.word(es, 0));
  S.cap = A.word(cap, 0);
  S.targetShares = A.word(ts, 0);
  S.shareCap = A.word(sc, 0);
  S.constantsLoaded = true;
  void gen;
}

async function poll() {
  if (!S.contract || !S.rpc) return;
  const c = A.calldata;
  try {
    if (!S.constantsLoaded) await loadConstants();
    const fr = await A.ethCall(S.rpc, S.contract, c.frontier());
    const e = A.word(fr, 0), seed = A.wordHex(fr, 1), target = A.word(fr, 2);
    const [sup, tot, pool, sec] = await A.ethCallBatch(S.rpc, S.contract, [
      c.totalSupply(), c.totalShares(e), c.poolOf(e), c.secondsToNextEpoch()]);
    const flipped = S.epoch !== null && e !== S.epoch;
    S.supply = A.word(sup, 0);
    S.epochShares = A.word(tot, 0);
    S.pool = A.word(pool, 0);
    S.flipDeadline = now() + Number(A.word(sec, 0)) * 1000;
    S.seed = seed; S.target = target;
    if (flipped) onEpochFlip(e);
    S.epoch = e;
    $("rpc-dot").className = "dot ok";
    render();
  } catch (err) {
    $("rpc-dot").className = "dot bad";
    log(`rpc: ${err.message}`, "bad");
  }
}

function onEpochFlip(e) {
  log(`epoch ${e} — new seed, target 2^${bits(S.target)}`, "hot");
  $("gauge").classList.add("flash");
  setTimeout(() => $("gauge").classList.remove("flash"), 900);
  if (S.mining) {
    S.buf = [];                       // stale shares die at the flip
    S.worker.postMessage({ cmd: "retune", seed: S.seed, addr: S.account,
      target: "0x" + S.target.toString(16), startNonce: 1 });
    log("miner retuned to new epoch; stale buffer dropped");
  }
  if (S.account) refreshLedgerHead();
}

// ------------------------------------------------------------ render
function render() {
  $("epoch-num").textContent = S.epoch === null ? "—" : S.epoch.toString();
  $("pool-val").textContent = fmtAsh(S.pool, 2);
  const era = S.epoch === null ? 0n : S.epoch / 210000n;
  $("era-val").textContent = era.toString();
  $("halving-val").textContent = S.epoch === null ? "—"
    : ((era + 1n) * 210000n - S.epoch).toLocaleString();
  $("target-val").textContent = S.target ? "2^" + bits(S.target) : "—";
  $("prob-val").textContent = S.target
    ? "1 share ≈ 2^" + (256 - bits(S.target) - 1) + " hashes" : "";
  $("shares-val").textContent = `${S.epochShares} / ${S.targetShares}`;
  $("shares-bar").style.width =
    Math.min(100, Number(S.epochShares) / Number(S.shareCap) * 100) + "%";
  $("cap-note").textContent = `epoch caps at ${S.shareCap}`;
  $("supply-val").textContent = `${fmtAsh(S.supply, 2)} / ${fmtAsh(S.cap, 0)}`;
  $("supply-bar").style.width =
    (Number(S.supply) / Number(S.cap) * 100).toFixed(4) + "%";
  renderMiner();
}

function tickGauge() {
  const remain = Math.max(0, S.flipDeadline - now());
  const frac = S.epoch === null ? 0
    : 1 - remain / (S.epochSeconds * 1000);
  const deg = Math.max(0, Math.min(360, frac * 360));
  const heat = frac < .5
    ? `var(--ember)` : frac < .85 ? `var(--heat)` : `var(--whitehot)`;
  $("gauge").style.background =
    `conic-gradient(${heat} ${deg}deg, #241e17 ${deg}deg)`;
  const s = Math.floor(remain / 1000);
  $("countdown").textContent = S.epoch === null ? "" :
    `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

// ------------------------------------------------------------ wallet
async function connect() {
  if (!window.ethereum) { log("no wallet found — install MetaMask", "bad"); return; }
  const [acct] = await window.ethereum.request({ method: "eth_requestAccounts" });
  S.account = acct;
  $("connect-btn").textContent = short(acct);
  await ensureChain();
  refreshLedgerHead();
  log(`wallet ${short(acct)} connected`);
}

async function ensureChain() {
  const net = NETS[S.net];
  if (!net || !net.chainId || !window.ethereum) { S.chainOk = !!S.account; return; }
  const cur = await window.ethereum.request({ method: "eth_chainId" });
  if (cur === net.chainId) { S.chainOk = true; return; }
  try {
    await window.ethereum.request({ method: "wallet_switchEthereumChain",
      params: [{ chainId: net.chainId }] });
    S.chainOk = true;
  } catch (err) {
    if (err.code === 4902 && net.add) {
      await window.ethereum.request({ method: "wallet_addEthereumChain",
        params: [{ chainId: net.chainId, ...net.add }] });
      S.chainOk = true;
    } else { S.chainOk = false; log(`switch chain: ${err.message}`, "bad"); }
  }
}

async function sendTx(data, label) {
  if (!S.account) throw new Error("connect a wallet first");
  await ensureChain();
  S.txPending = true; render();
  try {
    const hash = await window.ethereum.request({ method: "eth_sendTransaction",
      params: [{ from: S.account, to: S.contract, data }] });
    log(`${label} → ${short(hash)}`, "hot");
    return hash;
  } finally { S.txPending = false; render(); }
}

// ------------------------------------------------------------ mining
function makeWorker() {
  const blob = new Blob([WORKER_SRC], { type: "text/javascript" });
  return new Worker(URL.createObjectURL(blob));
}

function startMining() {
  if (!S.account) { log("connect a wallet — shares are bound to your address", "bad"); return; }
  if (!S.seed) { log("no frontier yet — check RPC/contract", "bad"); return; }
  if (!S.worker) {
    S.worker = makeWorker();
    let emaT = 0, emaN = 0;
    S.worker.onmessage = (ev) => {
      const m = ev.data;
      S.tried += m.tried;
      emaT = emaT * .8 + m.ms * .2; emaN = emaN * .8 + m.tried * .2;
      S.hashrate = emaT ? Math.round(emaN / (emaT / 1000)) : 0;
      if (m.found.length) {
        S.buf.push(...m.found.map(BigInt));
        log(`+${m.found.length} share${m.found.length > 1 ? "s" : ""} found (buffer ${S.buf.length})`);
      }
      maybeAutoSubmit();
      renderMiner();
    };
  }
  S.mining = true; S.tried = 0; S.buf = [];
  S.worker.postMessage({ cmd: "start", seed: S.seed, addr: S.account,
    target: "0x" + S.target.toString(16), startNonce: 1 });
  log(`burning against epoch ${S.epoch} at 2^${bits(S.target)}`);
  renderMiner();
}

function stopMining() {
  S.mining = false;
  if (S.worker) S.worker.postMessage({ cmd: "stop" });
  renderMiner();
}

function maybeAutoSubmit() {
  if (!S.autoSubmit || S.submitting || S.txPending) return;
  const nearFlip = S.flipDeadline - now() < 45000;
  if (S.buf.length >= 16 || (nearFlip && S.buf.length > 0)) submitShares();
}

async function submitShares() {
  if (!S.buf.length || S.submitting) return;
  S.submitting = true;
  const batch = S.buf.sort((a, b) => (a < b ? -1 : 1)).slice(0, 64);
  try {
    await sendTx(A.calldata.submitShares(batch), `submit ${batch.length} shares`);
    S.buf = S.buf.filter((n) => !batch.includes(n));
  } catch (err) {
    const m = err.message || "";
    if (m.includes("no work")) {
      log("epoch flipped mid-submit — stale shares dropped", "bad");
      S.buf = [];
    } else if (m.includes("epoch full")) {
      log("epoch full (SHARE_CAP) — standing down until next epoch", "bad");
      S.buf = [];
    } else log(`submit: ${m}`, "bad");
  } finally { S.submitting = false; renderMiner(); }
}

function renderMiner() {
  $("mine-btn").textContent = S.mining ? "Stop burning" : "Start burning";
  $("mine-btn").classList.toggle("hotbtn", !S.mining);
  $("hashrate").textContent = S.mining ? S.hashrate.toLocaleString() + " H/s" : "idle";
  $("found-count").textContent = S.buf.length.toString();
  const perShare = S.target && S.hashrate
    ? Number((1n << 256n) / S.target) / S.hashrate : null;
  $("est-share").textContent = !S.mining ? "" : perShare
    ? (perShare > 1 ? `~${Math.round(perShare)}s / share at this rate` : "raining shares")
    : "";
  $("submit-btn").textContent = `Submit ${Math.min(S.buf.length, 64)} shares`;
  $("submit-btn").disabled = !S.buf.length || S.submitting || S.txPending;
  $("miner-note").textContent =
    "In-page reference burner (~5 kH/s). Real hashrate: the pod/GPU miner in the repo.";
}

// ------------------------------------------------------------ ledger
async function refreshLedgerHead() {
  const addr = S.watchAddr || S.account;
  if (!addr || !S.contract) return;
  try {
    const c = A.calldata;
    const [bal, mine] = await A.ethCallBatch(S.rpc, S.contract, [
      c.balanceOf(addr), c.sharesOf(S.epoch ?? 0n, addr)]);
    $("bal-val").textContent = fmtAsh(A.word(bal, 0)) + " ASH";
    $("myshares-val").textContent = A.word(mine, 0).toString();
  } catch (_) {}
}

async function scanClaims() {
  const addr = S.watchAddr || S.account;
  if (!addr || S.epoch === null) return;
  $("scan-btn").disabled = true;
  $("scan-btn").textContent = "Scanning…";
  const c = A.calldata;
  const rows = [];
  const from = S.epoch > 64n ? S.epoch - 64n : 0n;
  try {
    for (let e = S.epoch - 1n; e + 1n > from; e -= 1n) {
      if (e < 0n) break;
      const [sh, cl, tot, pool] = await A.ethCallBatch(S.rpc, S.contract, [
        c.sharesOf(e, addr), c.claimed(e, addr),
        c.totalShares(e), c.poolOf(e)]);
      const mine = A.word(sh, 0);
      if (mine > 0n && A.word(cl, 0) === 0n) {
        const est = A.word(pool, 0) * mine / A.word(tot, 0);
        rows.push({ e, mine, est });
      }
      if (e === 0n) break;
    }
  } catch (err) { log(`scan: ${err.message}`, "bad"); }
  S.claimRows = rows;
  const body = $("claims-body");
  body.innerHTML = "";
  for (const r of rows) {
    const div = document.createElement("div");
    div.className = "row";
    div.innerHTML = `<span>epoch ${r.e}</span><span>${r.mine} shares</span>` +
      `<span class="mono">${fmtAsh(r.est)} ASH</span>`;
    body.appendChild(div);
  }
  $("claims-empty").hidden = rows.length > 0;
  $("claim-all").hidden = rows.length === 0;
  $("claim-all").textContent = `Claim ${rows.length} epoch${rows.length > 1 ? "s" : ""} (${fmtAsh(rows.reduce((a, r) => a + r.est, 0n))} ASH)`;
  $("scan-btn").disabled = false;
  $("scan-btn").textContent = "Scan last 64 epochs";
}

async function claimAll() {
  if (!S.claimRows.length) return;
  const es = S.claimRows.map((r) => r.e);
  try {
    await sendTx(A.calldata.claimMany(es), `claim ${es.length} epochs`);
    setTimeout(() => { refreshLedgerHead(); scanClaims(); }, 4000);
  } catch (err) { log(`claim: ${err.message}`, "bad"); }
}

// ------------------------------------------------------------ boot
function boot() {
  readConfig(); applyConfigUI();

  $("net-select").onchange = (e) => {
    S.net = e.target.value;
    if (NETS[S.net] && NETS[S.net].rpc) S.rpc = NETS[S.net].rpc;
    applyConfigUI();
  };
  $("save-config").onclick = () => {
    S.rpc = $("rpc-input").value.trim();
    S.contract = $("contract-input").value.trim();
    S.constantsLoaded = false; S.epoch = null;
    writeConfig(); applyConfigUI();
    log(`panel pointed at ${short(S.contract)} via ${S.net}`);
    poll();
  };
  $("connect-btn").onclick = connect;
  $("mine-btn").onclick = () => (S.mining ? stopMining() : startMining());
  $("submit-btn").onclick = submitShares;
  $("autosub").onchange = (e) => { S.autoSubmit = e.target.checked; };
  $("scan-btn").onclick = scanClaims;
  $("claim-all").onclick = claimAll;

  if (window.ethereum) {
    window.ethereum.on?.("accountsChanged", (a) => {
      S.account = a[0] || null;
      $("connect-btn").textContent = S.account ? short(S.account) : "Connect wallet";
      if (S.mining) { stopMining(); log("account changed — burner stopped"); }
    });
    window.ethereum.on?.("chainChanged", () => { S.chainOk = false; });
  }

  if (S.watchAddr) {
    $("connect-btn").textContent = "👁 " + short(S.watchAddr);
    $("connect-btn").disabled = true;
    $("mine-btn").disabled = true;
    $("submit-btn").disabled = true;
    log("watch mode — monitoring " + short(S.watchAddr));
    setInterval(refreshLedgerHead, 15000);
    setInterval(() => { if (S.epoch !== null) scanClaims(); }, 60000);
  } else {
    log("ASH furnace live on Bittensor EVM — connect a wallet to mine");
  }
  poll();
  setInterval(poll, 4000);
  setInterval(tickGauge, 250);
}
document.addEventListener("DOMContentLoaded", boot);
