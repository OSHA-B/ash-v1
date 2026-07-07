// deploy.js — deploy ASH with ethers v6. TESTNET FIRST.
//
//   RPC_URL=https://test.chain.opentensor.ai \
//   PRIVATE_KEY=0x… INITIAL_TARGET=0 node deploy.js
//
// Verified chain params (docs.learnbittensor.org, chainid.network, 2026-07-07):
//   Bittensor EVM mainnet: chainId 964, https://lite.chain.opentensor.ai
//   Bittensor EVM testnet: chainId 945, https://test.chain.opentensor.ai
// The contract is chain-agnostic EVM; Base works identically.
// UNTESTED against a live node — deploy to testnet, then round-trip ONE
// share with live_loop.py before anything real. Deploy = freeze, forever.
const { ethers } = require("ethers");
const fs = require("fs");

(async () => {
  const rpc = process.env.RPC_URL, pk = process.env.PRIVATE_KEY;
  if (!rpc || !pk) throw new Error("set RPC_URL and PRIVATE_KEY");
  const initialTarget = BigInt(process.env.INITIAL_TARGET || "0"); // 0 = 1-in-2^20

  const provider = new ethers.JsonRpcProvider(rpc);
  const net = await provider.getNetwork();
  console.log(`chain id ${net.chainId} via ${rpc}`);

  const wallet = new ethers.Wallet(pk, provider);
  console.log(`deployer ${wallet.address} balance ${ethers.formatEther(await provider.getBalance(wallet.address))}`);

  const abi = JSON.parse(fs.readFileSync("out_ASH.abi.json", "utf8"));
  const bytecode = "0x" + fs.readFileSync("out_ASH.bin", "utf8").trim();
  const factory = new ethers.ContractFactory(abi, bytecode, wallet);

  const ash = await factory.deploy(initialTarget);
  console.log(`deploy tx ${ash.deploymentTransaction().hash}`);
  await ash.waitForDeployment();
  const addr = await ash.getAddress();
  console.log(`\nASH deployed — and frozen — at ${addr}`);

  const [e, seed, target] = await ash.frontier();
  console.log(`frontier: epoch ${e}, seed ${seed}, target 2^${(target.toString(2).length - 1)}`);
  console.log(`cap ${ethers.formatEther(await ash.CAP())} | pool ${ethers.formatEther(await ash.poolOf(0))}/epoch | epoch ${await ash.EPOCH_SECONDS()}s`);
  console.log(`\nnext: python3 py/live_loop.py --rpc ${rpc} --contract ${addr} --key $PK --market mock`);
})().catch((e) => { console.error(e); process.exit(1); });
