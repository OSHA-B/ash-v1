// frontend/test.js — verify ashlib against the python oracle and ethers.
const A = require("./src/ashlib.js");
const { ethers } = require("ethers");
const V = require("./test/vectors.json");

// 1) keccak vs pycryptodome-derived vectors
for (const v of V.keccak) {
  const got = A.bytesToHex(A.keccak256(A.hexToBytes(v.in))).slice(2);
  if (got !== v.out) throw new Error(`keccak mismatch len ${v.in.length/2}`);
}
console.log(`✓ JS keccak matches python oracle on ${V.keccak.length} vectors`);

// 2) share preimage parity (the mining-critical path)
for (const s of V.share) {
  const h = A.shareHash(A.hexToBytes(s.seed), A.hexToBytes(s.addr), BigInt(s.nonce));
  if ("0x" + h.toString(16) !== s.hash) throw new Error("share hash mismatch");
}
console.log(`✓ JS share preimage == contract mirror on ${V.share.length} vectors`);

// 3) selectors + calldata byte-exact vs ethers' independent encoder
const abi = require("../out_ASH.abi.json");
const iface = new ethers.Interface(abi);
const addr = "0x" + "a1".repeat(20);
const cases = [
  ["frontier", [], A.calldata.frontier()],
  ["totalSupply", [], A.calldata.totalSupply()],
  ["poolOf", [7n], A.calldata.poolOf(7n)],
  ["totalShares", [12n], A.calldata.totalShares(12n)],
  ["balanceOf", [addr], A.calldata.balanceOf(addr)],
  ["sharesOf", [3n, addr], A.calldata.sharesOf(3n, addr)],
  ["claimed", [3n, addr], A.calldata.claimed(3n, addr)],
  ["submitShares", [[1n, 99n, 2n**200n]], A.calldata.submitShares([1n, 99n, 2n**200n])],
  ["claim", [5n], A.calldata.claim(5n)],
  ["claimMany", [[0n, 4n, 9n]], A.calldata.claimMany([0n, 4n, 9n])],
  ["roll", [], A.calldata.roll()],
  ["genesis", [], A.calldata.genesis()],
  ["secondsToNextEpoch", [], A.calldata.secondsToNextEpoch()],
];
for (const [fn, args, mine] of cases) {
  const ref = iface.encodeFunctionData(fn, args);
  if (mine !== ref) throw new Error(`calldata mismatch ${fn}: ${mine} != ${ref}`);
}
console.log(`✓ calldata byte-exact vs ethers on ${cases.length} functions`);

// 4) decode: frontier() return shape via ethers-encoded sample
const enc = ethers.AbiCoder.defaultAbiCoder().encode(
  ["uint64", "bytes32", "uint256"], [412n, "0x" + "ab".repeat(32), (1n << 244n)]);
if (A.word(enc, 0) !== 412n) throw new Error("decode e");
if (A.wordHex(enc, 1) !== "0x" + "ab".repeat(32)) throw new Error("decode seed");
if (A.word(enc, 2) !== (1n << 244n)) throw new Error("decode target");
console.log("✓ frontier() decode matches ethers encoding");

// 5) browser-miner hashrate estimate
const seed = A.hexToBytes("0x" + "11".repeat(32)), a20 = A.hexToBytes(addr);
let n = 1n, t0 = Date.now();
while (Date.now() - t0 < 1000) { A.shareHash(seed, a20, n); n++; }
console.log(`✓ in-page miner reference rate: ~${n.toString()} H/s (BigInt lanes; demo-grade by design)`);
console.log("ALL FRONTEND LIB TESTS PASSED");
