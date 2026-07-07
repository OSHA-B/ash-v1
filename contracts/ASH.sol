// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// ============================================================================
///  ASH — proof of burned compute.
///
///  A zero-utility, ownerless, mineable ERC-20.
///
///  The only way ASH comes into existence: grind keccak256 on a GPU until
///  hash(seed ‖ your address ‖ nonce) < shareTarget, and submit the nonce.
///  Every 600-second epoch pays a fixed pool, split pro-rata across all
///  valid shares submitted during that epoch. Emission halves every
///  210,000 epochs. Hard cap 21,000,000 ASH. Epochs with zero shares mint
///  nothing, forever.
///
///  Design invariants (why the contract can be frozen safely):
///
///  1. NO OWNER, NO ADMIN, NO UPGRADES. There is no privileged address
///     anywhere in this file. The deployer receives nothing.
///
///  2. NO EXTERNAL DEPENDENCIES. No oracle, no other token, no precompile,
///     no library import. Nothing this contract relies on can die, migrate,
///     or lie. keccak256 and block.timestamp only.
///
///  3. DIFFICULTY THROTTLES GAS, NOT EMISSION. Emission is purely
///     time-scheduled (pool per epoch). shareTarget only keeps the number
///     of on-chain share submissions in a sane band. Because difficulty
///     does not control issuance, a deliberately crude bang-bang retarget
///     (×2 / ÷2, hard-bounded) is safe — and crude means unbrickable.
///
///  4. DEADLOCK IS IMPOSSIBLE. Every fully-empty elapsed epoch eases the
///     prospective target by one doubling (capped), and the easing is
///     computed deterministically from elapsed time — visible via
///     frontier() before any transaction happens. If mining ever stops,
///     the puzzle drifts easier until a laptop can mine it again.
///
///  5. WORK IS ADDRESS-BOUND. msg.sender is inside the preimage, so shares
///     cannot be stolen from the mempool or front-run.
///
///  6. SEED IS STATE-CHAINED. Each epoch's seed commits to the previous
///     epoch's final share count, so shares for future epochs cannot be
///     precomputed while anyone at all is mining.
/// ============================================================================

contract ASH {
    // ---------------------------------------------------------------- ERC-20
    string public constant name = "ASH";
    string public constant symbol = "ASH";
    uint8 public constant decimals = 18;

    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    function transfer(address to, uint256 value) external returns (bool) {
        _transfer(msg.sender, to, value);
        return true;
    }

    function approve(address spender, uint256 value) external returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        uint256 a = allowance[from][msg.sender];
        if (a != type(uint256).max) {
            require(a >= value, "ASH: allowance");
            unchecked { allowance[from][msg.sender] = a - value; }
        }
        _transfer(from, to, value);
        return true;
    }

    function _transfer(address from, address to, uint256 value) internal {
        require(to != address(0), "ASH: zero to");
        uint256 b = balanceOf[from];
        require(b >= value, "ASH: balance");
        unchecked { balanceOf[from] = b - value; }
        balanceOf[to] += value;
        emit Transfer(from, to, value);
    }

    // ------------------------------------------------------------ Parameters
    uint256 public constant EPOCH_SECONDS   = 600;           // 10 minutes
    uint256 public constant INITIAL_POOL    = 50e18;          // 50 ASH / epoch
    uint256 public constant HALVING_EPOCHS  = 210_000;        // ~4 years
    uint256 public constant CAP             = 21_000_000e18;  // hard cap
    uint256 public constant TARGET_SHARES   = 512;            // per-epoch gas throttle
    uint256 public constant SHARE_CAP       = TARGET_SHARES * 16; // hard per-epoch bound
    uint256 public constant MAX_BATCH       = 64;             // nonces per tx
    uint256 public constant MAX_EASING      = 64;             // doublings per roll

    // Easiest allowed puzzle: ~1 in 256 hashes passes. Guarantees a CPU can
    // always mine a share within seconds once difficulty has fully eased.
    uint256 public constant MAX_TARGET = type(uint256).max >> 8;
    // Hardest allowed puzzle. A pure sanity bound; unreachable in practice.
    uint256 public constant MIN_TARGET = 1 << 32;

    uint64 public immutable genesis;

    // ---------------------------------------------------------- Mining state
    uint64  public lastRolledEpoch;  // epoch the current seed/target apply to
    bytes32 public epochSeed;
    uint256 public shareTarget;

    mapping(uint64 => uint64) public totalShares;
    mapping(uint64 => mapping(address => uint64))  public sharesOf;
    mapping(uint64 => mapping(address => uint256)) public lastNonce;
    mapping(uint64 => mapping(address => bool))    public claimed;

    event Rolled(uint64 indexed epoch, bytes32 seed, uint256 target, uint64 prevEpochShares);
    event Shares(address indexed miner, uint64 indexed epoch, uint64 count, uint64 epochTotal);
    event Claimed(address indexed miner, uint64 indexed epoch, uint256 amount);

    /// @param initialTarget starting share target; pass 0 for the default
    /// (~1 in 2^20 hashes — trivial for one GPU, so genesis is minable by
    /// anyone and the retarget walks difficulty up from there).
    constructor(uint256 initialTarget) {
        genesis = uint64(block.timestamp);
        uint256 t = initialTarget == 0 ? (type(uint256).max >> 20) : initialTarget;
        require(t >= MIN_TARGET && t <= MAX_TARGET, "ASH: target bounds");
        shareTarget = t;
        epochSeed = keccak256(abi.encodePacked("ASH/genesis", block.timestamp, address(this)));
    }

    // ---------------------------------------------------------------- Epochs
    function currentEpoch() public view returns (uint64) {
        return uint64((block.timestamp - genesis) / EPOCH_SECONDS);
    }

    function secondsToNextEpoch() external view returns (uint256) {
        return EPOCH_SECONDS - ((block.timestamp - genesis) % EPOCH_SECONDS);
    }

    /// Emission pool for epoch `e`. Halves every HALVING_EPOCHS.
    function poolOf(uint64 e) public pure returns (uint256) {
        uint256 era = e / HALVING_EPOCHS;
        return era >= 64 ? 0 : INITIAL_POOL >> era;
    }

    /// Pure retarget + reseed rule, shared verbatim by _roll() and frontier().
    ///
    /// Bang-bang controller:
    ///   prev epoch > 2× target shares  -> halve target (harder)
    ///   prev epoch < ½× target shares  -> double target (easier)
    /// plus one doubling per fully-empty elapsed epoch (deadlock escape),
    /// all hard-clamped to [MIN_TARGET, MAX_TARGET].
    function _nextParams(
        bytes32 prevSeed,
        uint256 prevTarget,
        uint64  prevEpoch,
        uint64  prevShares,
        uint64  nowEpoch
    ) internal pure returns (bytes32 seed, uint256 target) {
        target = prevTarget;

        if (prevShares > TARGET_SHARES * 2) {
            target >>= 1;
        } else if (prevShares > 0 && uint256(prevShares) * 2 < TARGET_SHARES) {
            target = target > (MAX_TARGET >> 1) ? MAX_TARGET : target << 1;
        }

        uint64 gap = nowEpoch - prevEpoch;                    // >= 1
        uint64 empties = prevShares == 0 ? gap : gap - 1;     // fully idle epochs
        if (empties > 0) {
            uint256 sh = empties > MAX_EASING ? MAX_EASING : uint256(empties);
            target = target > (MAX_TARGET >> sh) ? MAX_TARGET : target << sh;
        }

        if (target < MIN_TARGET) target = MIN_TARGET;
        if (target > MAX_TARGET) target = MAX_TARGET;

        seed = keccak256(abi.encodePacked(prevSeed, prevEpoch, prevShares, target, nowEpoch));
    }

    /// The (epoch, seed, target) a submission in this block would be judged
    /// against — i.e. the roll applied virtually. Miners mine against this.
    function frontier() public view returns (uint64 e, bytes32 seed, uint256 target) {
        e = currentEpoch();
        if (e == lastRolledEpoch) return (e, epochSeed, shareTarget);
        (seed, target) = _nextParams(
            epochSeed, shareTarget, lastRolledEpoch, totalShares[lastRolledEpoch], e
        );
    }

    /// Advance stored epoch state. Callable by anyone; also runs implicitly
    /// on every submission. Idempotent within an epoch.
    function roll() public {
        uint64 e = currentEpoch();
        uint64 prev = lastRolledEpoch;
        if (e == prev) return;
        uint64 s = totalShares[prev];
        (bytes32 seed, uint256 target) = _nextParams(epochSeed, shareTarget, prev, s, e);
        epochSeed = seed;
        shareTarget = target;
        lastRolledEpoch = e;
        emit Rolled(e, seed, target, s);
    }

    // ---------------------------------------------------------------- Mining
    /// Submit proof-of-work shares for the current epoch.
    /// Each nonce must satisfy:
    ///   uint256(keccak256(abi.encodePacked(epochSeed, msg.sender, nonce))) < shareTarget
    /// Nonces must be strictly increasing per miner per epoch (replay guard).
    function submitShares(uint256[] calldata nonces) external {
        roll();
        uint64 e = lastRolledEpoch;

        uint256 n = nonces.length;
        require(n > 0 && n <= MAX_BATCH, "ASH: batch size");

        bytes32 seed = epochSeed;
        uint256 target = shareTarget;
        uint256 last = lastNonce[e][msg.sender];

        for (uint256 i; i < n; ++i) {
            uint256 x = nonces[i];
            require(x > last, "ASH: nonce order");
            require(
                uint256(keccak256(abi.encodePacked(seed, msg.sender, x))) < target,
                "ASH: no work"
            );
            last = x;
        }

        lastNonce[e][msg.sender] = last;
        uint64 c = uint64(n);
        // Hard bound on per-epoch state. A sudden 1000x hashrate spike would
        // otherwise flood an epoch with shares before the once-per-epoch
        // halving reacts. Past the cap the epoch is full; difficulty catches
        // up on subsequent rolls. Bounded state forever > perfect fairness
        // during a spike.
        require(uint256(totalShares[e]) + c <= SHARE_CAP, "ASH: epoch full");
        sharesOf[e][msg.sender] += c;
        totalShares[e] += c;
        emit Shares(msg.sender, e, c, totalShares[e]);
    }

    // --------------------------------------------------------------- Claims
    /// Mint your pro-rata slice of a finished epoch's pool. Never expires.
    function claim(uint64 e) public {
        require(e < currentEpoch(), "ASH: epoch live");
        require(!claimed[e][msg.sender], "ASH: claimed");
        uint64 mine = sharesOf[e][msg.sender];
        require(mine > 0, "ASH: no shares");

        claimed[e][msg.sender] = true;
        uint256 amount = poolOf(e) * mine / totalShares[e];

        require(totalSupply + amount <= CAP, "ASH: cap");
        totalSupply += amount;
        balanceOf[msg.sender] += amount;
        emit Transfer(address(0), msg.sender, amount);
        emit Claimed(msg.sender, e, amount);
    }

    function claimMany(uint64[] calldata epochs) external {
        for (uint256 i; i < epochs.length; ++i) claim(epochs[i]);
    }
}
