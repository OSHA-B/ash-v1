/* ashlib.js — zero-dependency core for the ASH frontend.
 * keccak-256 (BigInt lanes, ported 1:1 from the repo's verified python),
 * minimal ABI encode/decode for exactly the calls ASH needs, JSON-RPC.
 * Verified in frontend/test.js: keccak vs pycryptodome vectors, selectors
 * and calldata byte-exact vs ethers' encoder. */
(function (root) {
  "use strict";

  // ---------------------------------------------------------- keccak-256
  const RC = [0x1n, 0x8082n, 0x800000000000808An, 0x8000000080008000n,
    0x808Bn, 0x80000001n, 0x8000000080008081n, 0x8000000000008009n,
    0x8An, 0x88n, 0x80008009n, 0x8000000An, 0x8000808Bn,
    0x800000000000008Bn, 0x8000000000008089n, 0x8000000000008003n,
    0x8000000000008002n, 0x8000000000000080n, 0x800An,
    0x800000008000000An, 0x8000000080008081n, 0x8000000000008080n,
    0x80000001n, 0x8000000080008008n];
  const ROT = [0n,1n,62n,28n,27n,36n,44n,6n,55n,20n,3n,10n,43n,25n,39n,
    41n,45n,15n,21n,8n,18n,2n,61n,56n,14n];
  const M64 = (1n << 64n) - 1n;
  const rol = (v, n) => n === 0n ? v : (((v << n) | (v >> (64n - n))) & M64);

  function keccakF(st) {
    for (let r = 0; r < 24; r++) {
      const c = [0n, 0n, 0n, 0n, 0n];
      for (let x = 0; x < 5; x++)
        c[x] = st[x] ^ st[x+5] ^ st[x+10] ^ st[x+15] ^ st[x+20];
      const d = [0n, 0n, 0n, 0n, 0n];
      for (let x = 0; x < 5; x++)
        d[x] = c[(x+4)%5] ^ rol(c[(x+1)%5], 1n);
      for (let i = 0; i < 25; i++) st[i] ^= d[i%5];
      const b = new Array(25).fill(0n);
      for (let x = 0; x < 5; x++)
        for (let y = 0; y < 5; y++)
          b[y + 5*((2*x + 3*y) % 5)] = rol(st[x + 5*y], ROT[x + 5*y]);
      for (let i = 0; i < 25; i++) {
        const row = (i/5|0)*5;
        st[i] = b[i] ^ ((~b[row + ((i%5)+1)%5]) & b[row + ((i%5)+2)%5] & M64);
      }
      st[0] ^= RC[r];
    }
    return st;
  }

  function keccak256(data) {                       // Uint8Array -> Uint8Array
    const rate = 136;
    let p;
    if ((data.length + 1) % rate === 0) {
      p = new Uint8Array(data.length + 1); p.set(data); p[data.length] = 0x81;
    } else {
      const padLen = rate - (data.length % rate);
      p = new Uint8Array(data.length + padLen); p.set(data);
      p[data.length] = 0x01; p[p.length - 1] |= 0x80;
    }
    let st = new Array(25).fill(0n);
    for (let off = 0; off < p.length; off += rate) {
      for (let i = 0; i < 17; i++) {
        let lane = 0n;
        for (let j = 7; j >= 0; j--)
          lane = (lane << 8n) | BigInt(p[off + i*8 + j]);
        st[i] ^= lane;
      }
      st = keccakF(st);
    }
    const out = new Uint8Array(32);
    for (let i = 0; i < 4; i++) {
      let lane = st[i];
      for (let j = 0; j < 8; j++) { out[i*8 + j] = Number(lane & 0xFFn); lane >>= 8n; }
    }
    return out;
  }

  // ---------------------------------------------------------- bytes / hex
  const hexToBytes = (h) => {
    h = h.startsWith("0x") ? h.slice(2) : h;
    const out = new Uint8Array(h.length / 2);
    for (let i = 0; i < out.length; i++)
      out[i] = parseInt(h.substr(i*2, 2), 16);
    return out;
  };
  const bytesToHex = (b) =>
    "0x" + Array.from(b, x => x.toString(16).padStart(2, "0")).join("");
  const bytesToBig = (b) => BigInt(bytesToHex(b));
  const concat = (...arrs) => {
    const out = new Uint8Array(arrs.reduce((n, a) => n + a.length, 0));
    let o = 0; for (const a of arrs) { out.set(a, o); o += a.length; }
    return out;
  };
  const be32 = (v) => {                            // BigInt -> 32-byte BE
    const out = new Uint8Array(32);
    let x = BigInt(v);
    for (let i = 31; i >= 0; i--) { out[i] = Number(x & 0xFFn); x >>= 8n; }
    return out;
  };
  const utf8 = (s) => new TextEncoder().encode(s);

  // ------------------------------------------------------------- ABI codec
  const selector = (sig) => bytesToHex(keccak256(utf8(sig))).slice(0, 10);
  const W = (v) => BigInt(v).toString(16).padStart(64, "0");
  const encAddr = (a) => a.toLowerCase().replace("0x", "").padStart(64, "0");
  const encUintArray = (arr) =>
    W(32) + W(arr.length) + arr.map(W).join("");
  const word = (hex, i) => {                        // i-th 32-byte word
    const s = (hex || "").replace("0x", "").substr(i * 64, 64);
    return s.length === 64 ? BigInt("0x" + s) : 0n;
  };
  const wordHex = (hex, i) =>
    "0x" + hex.replace("0x", "").substr(i * 64, 64);

  // exactly the ASH surface the page uses
  const calldata = {
    frontier:            () => selector("frontier()"),
    totalSupply:         () => selector("totalSupply()"),
    genesis:             () => selector("genesis()"),
    EPOCH_SECONDS:       () => selector("EPOCH_SECONDS()"),
    CAP:                 () => selector("CAP()"),
    TARGET_SHARES:       () => selector("TARGET_SHARES()"),
    SHARE_CAP:           () => selector("SHARE_CAP()"),
    currentEpoch:        () => selector("currentEpoch()"),
    secondsToNextEpoch:  () => selector("secondsToNextEpoch()"),
    poolOf:        (e)      => selector("poolOf(uint64)") + W(e),
    totalShares:   (e)      => selector("totalShares(uint64)") + W(e),
    balanceOf:     (a)      => selector("balanceOf(address)") + encAddr(a),
    sharesOf:      (e, a)   => selector("sharesOf(uint64,address)") + W(e) + encAddr(a),
    claimed:       (e, a)   => selector("claimed(uint64,address)") + W(e) + encAddr(a),
    submitShares:  (nonces) => selector("submitShares(uint256[])") + encUintArray(nonces),
    claim:         (e)      => selector("claim(uint64)") + W(e),
    claimMany:     (es)     => selector("claimMany(uint64[])") + encUintArray(es),
    roll:                () => selector("roll()"),
  };

  // the contract's share preimage: keccak(seed ‖ addr ‖ nonce)
  const shareHash = (seedBytes, addrBytes, nonce) =>
    bytesToBig(keccak256(concat(seedBytes, addrBytes, be32(nonce))));

  // ------------------------------------------------------------- JSON-RPC
  async function rpc(url, method, params) {
    const r = await fetch(url, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
    });
    const j = await r.json();
    if (j.error) throw new Error(j.error.message || JSON.stringify(j.error));
    return j.result;
  }
  const ethCall = (url, to, data) =>
    rpc(url, "eth_call", [{ to, data }, "latest"]);

  async function ethCallBatch(url, to, datas) {     // batch, seq fallback
    try {
      const body = datas.map((d, i) => ({
        jsonrpc: "2.0", id: i, method: "eth_call", params: [{ to, data: d }, "latest"],
      }));
      const r = await fetch(url, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      const j = await r.json();
      if (!Array.isArray(j)) throw new Error("no batch support");
      const out = new Array(datas.length);
      for (const item of j) {
        if (item.error) throw new Error(item.error.message);
        out[item.id] = item.result;
      }
      return out;
    } catch (_) {
      const out = [];
      for (const d of datas) out.push(await ethCall(url, to, d));
      return out;
    }
  }

  const ASHLIB = { keccak256, hexToBytes, bytesToHex, bytesToBig, concat,
    be32, utf8, selector, W, encAddr, encUintArray, word, wordHex,
    calldata, shareHash, rpc, ethCall, ethCallBatch };
  if (typeof module !== "undefined" && module.exports) module.exports = ASHLIB;
  root.ASHLIB = ASHLIB;
})(typeof self !== "undefined" ? self : globalThis);
