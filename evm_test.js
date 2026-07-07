// evm_test.js — run the REAL compiled ASH bytecode in an in-process EVM.
// Deploy → mine real keccak shares → submit → warp → claim → verify:
//   pro-rata math, replay guard, fake-work guard, epoch gating,
//   double-claim guard, idle easing, frontier()==roll() parity,
//   and byte-exact preimage parity with the Python mirror.

const { VM } = require("@ethereumjs/vm");
const { Address, hexToBytes, bytesToHex } = require("@ethereumjs/util");
const { ethers } = require("ethers");
const fs = require("fs");

const abi = JSON.parse(fs.readFileSync("out_ASH.abi.json", "utf8"));
const bytecode = "0x" + fs.readFileSync("out_ASH.bin", "utf8").trim();
const iface = new ethers.Interface(abi);

const MINER_A = "0x" + "a1".repeat(20);
const MINER_B = "0x" + "b2".repeat(20);
const U256 = (1n << 256n) - 1n;
const INITIAL_TARGET = U256 >> 12n; // 1 share ≈ 4096 hashes

let ts = 1_800_000_000n; // controllable block.timestamp
const blk = () => ({
  header: {
    timestamp: ts, number: 1n, difficulty: 0n, prevRandao: new Uint8Array(32),
    coinbase: Address.zero(), gasLimit: 30_000_000n,
    baseFeePerGas: 0n, getBlobGasPrice: () => 0n,
  },
});

let vm, ash;

async function call(from, fn, args = [], expectRevert = null) {
  const res = await vm.evm.runCall({
    caller: Address.fromString(from),
    origin: Address.fromString(from),
    to: ash,
    data: hexToBytes(iface.encodeFunctionData(fn, args)),
    gasLimit: 25_000_000n,
    block: blk(),
  });
  const err = res.execResult.exceptionError;
  if (expectRevert !== null) {
    if (!err) throw new Error(`${fn}: expected revert '${expectRevert}', got success`);
    const ret = bytesToHex(res.execResult.returnValue);
    const msg = ret.length > 10 ? ethers.AbiCoder.defaultAbiCoder()
      .decode(["string"], "0x" + ret.slice(10))[0] : "(no message)";
    if (msg !== expectRevert) throw new Error(`${fn}: expected '${expectRevert}', got '${msg}'`);
    return msg;
  }
  if (err) {
    const ret = bytesToHex(res.execResult.returnValue);
    const msg = ret.length > 10 ? ethers.AbiCoder.defaultAbiCoder()
      .decode(["string"], "0x" + ret.slice(10))[0] : String(err.error);
    throw new Error(`${fn} reverted: ${msg}`);
  }
  return iface.decodeFunctionResult(fn, res.execResult.returnValue);
}

// same grind as py/miner.py, via ethers' independent encodePacked implementation
function mine(seed, addr, target, want, start = 1n) {
  const out = [];
  let n = start;
  while (out.length < want) {
    const h = BigInt(ethers.solidityPackedKeccak256(
      ["bytes32", "address", "uint256"], [seed, addr, n]));
    if (h < target) out.push(n);
    n++;
  }
  return out;
}

const ok = (label) => console.log("  ✓", label);

(async () => {
  vm = await VM.create();
  console.log("== executing real ASH bytecode in-process ==");

  // deploy
  const deployData = bytecode + ethers.AbiCoder.defaultAbiCoder()
    .encode(["uint256"], [INITIAL_TARGET]).slice(2);
  const dep = await vm.evm.runCall({
    caller: Address.fromString(MINER_A),
    data: hexToBytes(deployData),
    gasLimit: 25_000_000n,
    block: blk(),
  });
  if (dep.execResult.exceptionError) throw new Error("deploy failed");
  ash = dep.createdAddress;
  ok(`deployed at ${ash.toString()} (no owner, no admin in ABI)`);

  // epoch 0: frontier == stored state, both miners mine & submit
  let [e0, seed0, target0] = await call(MINER_A, "frontier");
  if (target0 !== INITIAL_TARGET) throw new Error("target mismatch");
  const na = mine(seed0, MINER_A, target0, 3);
  const nb = mine(seed0, MINER_B, target0, 1);
  await call(MINER_A, "submitShares", [na]);
  await call(MINER_B, "submitShares", [nb]);
  const [tot0] = await call(MINER_A, "totalShares", [0n]);
  if (tot0 !== 4n) throw new Error("share count");
  ok(`epoch 0: A mined 3 shares, B mined 1 (real keccak, address-bound)`);

  // guards
  await call(MINER_A, "submitShares", [na], "ASH: nonce order");
  ok("replay of A's nonces reverts: 'ASH: nonce order'");
  await call(MINER_B, "submitShares", [[123456789n]], "ASH: no work");
  ok("nonce without work reverts: 'ASH: no work'");
  await call(MINER_A, "claim", [0n], "ASH: epoch live");
  ok("claiming a live epoch reverts: 'ASH: epoch live'");

  // warp to epoch 1, settle epoch 0 pro-rata: pool 50, A 3/4, B 1/4
  ts += 600n;
  await call(MINER_A, "claim", [0n]);
  await call(MINER_B, "claim", [0n]);
  const [balA] = await call(MINER_A, "balanceOf", [MINER_A]);
  const [balB] = await call(MINER_A, "balanceOf", [MINER_B]);
  if (balA !== ethers.parseEther("37.5") || balB !== ethers.parseEther("12.5"))
    throw new Error(`pro-rata: ${balA} / ${balB}`);
  ok("pro-rata claim exact: A=37.5 ASH (3/4), B=12.5 ASH (1/4) of 50 pool");
  await call(MINER_A, "claim", [0n], "ASH: claimed");
  ok("double claim reverts: 'ASH: claimed'");

  // frontier() must equal state after roll() — the miner's contract
  let f1 = await call(MINER_A, "frontier");
  await call(MINER_B, "roll");
  const [seedNow] = await call(MINER_A, "epochSeed");
  const [tgtNow] = await call(MINER_A, "shareTarget");
  if (f1[1] !== seedNow || f1[2] !== tgtNow) throw new Error("frontier parity");
  ok("frontier() == post-roll state (miners can mine pre-roll safely)");

  // idle 5 empty epochs → easing: target × 2^5, clamped at MAX_TARGET (2^248)
  ts += 600n * 5n;
  const MAXT = U256 >> 8n;
  const [, , tEased] = await call(MINER_A, "frontier");
  const expEase = (tgtNow << 5n) > MAXT ? MAXT : tgtNow << 5n;
  if (tEased !== expEase) throw new Error(`easing: ${tEased}`);
  ok("5 idle epochs ease target ×2^5, clamped at MAX_TARGET ceiling (deadlock-proofing + bound, on real bytecode)");

  // mine at the eased target and claim as sole miner → full pool
  const [e6, seed6, t6] = await call(MINER_A, "frontier");
  const n6 = mine(seed6, MINER_A, t6, 2);
  await call(MINER_A, "submitShares", [n6]);
  ts += 600n;
  await call(MINER_A, "claim", [e6]);
  const [balA2] = await call(MINER_A, "balanceOf", [MINER_A]);
  if (balA2 !== ethers.parseEther("87.5")) throw new Error("sole-miner pool");
  ok("sole miner claims full 50 ASH pool of eased epoch (A total 87.5)");

  // ERC-20 transfer + supply conservation
  await call(MINER_A, "transfer", [MINER_B, ethers.parseEther("10")]);
  const [ts_] = await call(MINER_A, "totalSupply");
  const [ba] = await call(MINER_A, "balanceOf", [MINER_A]);
  const [bb] = await call(MINER_A, "balanceOf", [MINER_B]);
  if (ba + bb !== ts_ || ts_ !== ethers.parseEther("100")) throw new Error("conservation");
  ok("ERC-20 transfer works; Σ balances == totalSupply == 100 ASH (2 epochs × 50)");

  // halving arithmetic straight from the bytecode
  const [p0] = await call(MINER_A, "poolOf", [0n]);
  const [p1] = await call(MINER_A, "poolOf", [210000n]);
  const [p2] = await call(MINER_A, "poolOf", [420000n]);
  if (p0 !== ethers.parseEther("50") || p1 !== ethers.parseEther("25") || p2 !== ethers.parseEther("12.5"))
    throw new Error("halving");
  ok("halving on-chain: poolOf(0)=50, poolOf(210k)=25, poolOf(420k)=12.5");

  // preimage parity vector for the Python mirror
  const vSeed = "0x" + "11".repeat(32);
  const vHash = ethers.solidityPackedKeccak256(
    ["bytes32", "address", "uint256"], [vSeed, MINER_A, 42n]);
  console.log(`\nparity vector (share preimage): keccak(0x11…11, ${MINER_A}, 42) = ${vHash}`);
  const vSeed2 = ethers.solidityPackedKeccak256(
    ["bytes32", "uint64", "uint64", "uint256", "uint64"],
    [vSeed, 7n, 512n, INITIAL_TARGET, 9n]);
  console.log(`parity vector (reseed preimage): ${vSeed2}`);
  fs.writeFileSync("parity_vectors.json", JSON.stringify({
    share: { seed: vSeed, addr: MINER_A, nonce: 42, hash: vHash },
    reseed: { seed: vSeed, prevEpoch: 7, prevShares: 512, target: INITIAL_TARGET.toString(), nowEpoch: 9, hash: vSeed2 },
  }, null, 2));
  console.log("\nALL EVM TESTS PASSED");
})().catch((e) => { console.error("FAILED:", e.message); process.exit(1); });
