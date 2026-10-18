# Shared Cores: running some chains, borrowing the rest

An arcade does not have to run a Core node for every chain it offers. Any operator can share a Core
that runs on their machine with every other arcade, and any arcade can borrow a chain from arcades
that share it. Two arcades that each run two Cores can both offer every chain.

## The rule

- **Pepecoin testnet always runs on your own machine.** The Messenger, the @names and the mesh's own
  checks live there.
- **Run at least one more Core yourself.** Every arcade brings a Core to the network as well as
  borrowing.
- **Every other chain is your choice:** run it here, borrow it from an arcade that shares it, or (for
  a chain the arcade can do without, such as Dogecoin) leave it off.

For example, one operator runs Pepecoin testnet, Pepecoin mainnet and Dogecoin, and shares the two
mainnet Cores. Another runs Pepecoin testnet and Litecoin, and shares Litecoin. Both arcades then
offer every chain.

## Setting it up

Open **Admin → Chains**. Each chain has:

- **Core:** *Runs here*, *Borrow a shared Core*, or *Off*. Changing this takes effect when the arcade
  restarts; at the node machine the panel offers **Restart now** when a service manager runs the
  arcade.
- **Share:** let other arcades use this chain's Core. **On by default:** every Core that runs here is
  shared unless you untick it. Only a Core that runs here can be shared. A change takes effect within
  a minute, with no restart.

The same choices are kept in `settings.json` as `core:<chain>` (`here`, `borrow` or `off`) and
`share:<chain>` (`true` or `false`; `true` when it is not set).

## What sharing lets other arcades do

A shared Core answers a short list of requests and nothing else:

- reading blocks, transactions, block headers and the chain tip;
- reading the mempool and whether a coin is still unspent;
- fee estimates;
- broadcasting a transaction that somebody already signed.

**It never answers a wallet request.** Sharing can never spend, sign or reveal your node's coins. Your
Core works on at most two borrowed requests at a time, so your own pages never wait behind other
arcades, and each borrowing arcade is limited in how fast it can ask.

A sharing arcade also sends each new mempool transaction, as it arrives, to the arcades borrowing
that chain, so their wallets see incoming payments as quickly as yours does.

## What a borrowing arcade checks

A borrowed chain's answers come from somebody else's node, so the borrowing arcade proves what it can
from the bytes:

- **Transactions** are fetched raw. A transaction whose bytes hash to the id that was asked for is that
  transaction; nothing in it can have been changed.
- **Blocks** are fetched raw. The block's hash must be the hash of its header, its transactions must
  hash up to the header's merkle root, and the header must carry the proof of work its difficulty
  asks for: scrypt, or for a merge-mined chain (Dogecoin, Pepecoin mainnet) a parent block that
  commits to it. A block whose difficulty falls more than fourfold from the block under it is refused.
- **Mempool transactions** pushed by a sharer are checked the same way as any other transaction.

What cannot be proved from bytes is taken on the sharer's word: which block is at a given height,
whether a coin is still unspent, and what the mempool holds. When two sharing arcades are linked, their
chain tips are compared, and the admin panel says so if they disagree.

An answer that fails a check is refused. The sharer that sent it goes to the back of the line, and the
next sharing arcade is asked instead.

A borrowed chain has no node wallet. Accounts work as usual, because people sign in their own browsers.
What needs the node's own coins on that chain does not: the faucet, the node's own trading, and the node
paying for inscriptions.

## Every arcade's index, compared

Every arcade builds its index of tokens, inscriptions, names and trades from the chain alone, so honest
arcades on the same rules hold the same index. Linked arcades check that they do:

- Every minute, each arcade sends a fingerprint of its index up to a checkpoint (a multiple of 100
  blocks, at least 6 blocks under the tip, so a reorganisation cannot move it), and the consensus hash
  of its balances.
- Two arcades whose fingerprints differ halve the range of blocks, again and again, until they find the
  first block they read differently, and both arcades' rows for that block are shown in
  **Admin → Chains**.

This applies to borrowed chains too: an index built from a borrowed Core's blocks is compared with the
indexes other arcades built from their own Cores.
