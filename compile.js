const solc = require("solc"), fs = require("fs");
const src = fs.readFileSync("contracts/ASH.sol", "utf8");
const input = {
  language: "Solidity",
  sources: { "ASH.sol": { content: src } },
  settings: {
    optimizer: { enabled: true, runs: 5000 },
    outputSelection: { "*": { "*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"] } }
  }
};
const out = JSON.parse(solc.compile(JSON.stringify(input)));
let fatal = false;
for (const e of out.errors || []) {
  console.log(`[${e.severity}] ${e.formattedMessage.trim()}`);
  if (e.severity === "error") fatal = true;
}
if (fatal) process.exit(1);
const c = out.contracts["ASH.sol"]["ASH"];
fs.writeFileSync("out_ASH.abi.json", JSON.stringify(c.abi, null, 2));
fs.writeFileSync("out_ASH.bin", c.evm.bytecode.object);
console.log("COMPILE OK — optimizer 5000 runs");
console.log("deployed bytecode:", c.evm.deployedBytecode.object.length / 2, "bytes");
console.log("ABI functions:", c.abi.filter(x => x.type === "function").map(x => x.name).join(", "));
