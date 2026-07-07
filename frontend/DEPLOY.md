# ASH panel — deploy to the Internet Computer

The panel is a single self-contained file (no CDNs, no framework, 42 KB)
served from an ICP **asset canister**. It reads the ASH contract over
JSON-RPC from the browser and writes through the visitor's injected
wallet (MetaMask etc.) — the canister only serves bytes, so hosting is
fully on-chain while the token stays on its EVM chain.

## Prereqs
```bash
sh -ci "$(curl -fsSL https://internetcomputer.org/install.sh)"   # dfx
node build.js                                # src/ → dist/index.html
```
(`dfx deploy` also runs `node build.js` automatically via the build hook.)

## Local test
```bash
dfx start --background
dfx deploy
# open the printed URL, e.g. http://<canister-id>.localhost:4943
```

## IC mainnet
You need cycles on your identity (NNS dapp → Canisters → create, or the
cycles faucet for a first deploy).
```bash
dfx deploy --network ic
# → https://<canister-id>.icp0.io
```
Updating later: edit src/, `dfx deploy --network ic` again. Same canister,
new assets.

## Pointing the panel at a furnace
Settings drawer in the UI, or URL params it writes for you:
```
https://<canister-id>.icp0.io/?net=bittensor-test&contract=0xYOUR_ASH
```
Presets: Bittensor EVM mainnet (964, lite.chain.opentensor.ai),
testnet (945, test.chain.opentensor.ai), Base, or custom RPC.

## What's enforced
`dist/.ic-assets.json5` sets CSP (connect-src https: so any RPC works;
worker-src blob: for the in-page miner), X-Frame-Options DENY, nosniff,
no-referrer. The page stores config in the URL + localStorage only —
no keys ever touch it; transactions are signed by the visitor's wallet.

## Verified in this repo
`node test.js` — the page's keccak matches the python oracle (49 vectors),
share preimages match the contract mirror, calldata is byte-exact vs
ethers for all 13 functions used, and a DOM-stubbed smoke test boots the
built dist/index.html against a fake chain. The exact worker source
shipped in dist was executed and its nonces verified against the
contract rules. Untested live: a real wallet + real RPC in a real
browser — testnet shakes that out in minutes.
