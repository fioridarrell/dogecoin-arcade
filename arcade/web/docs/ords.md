# Inscribing ords from your wallet

**Creator → Ords** inscribes a file as an ord on Dogecoin (a Doginal), Pepecoin (a Pepinal), Bellscoin (a Bellinal) or
Luckycoin (a Luckinal). Your own wallet pays and signs, and the ord lands on your own address on that chain.

## How it works

1. Pick the chain and the file, and press **Inscribe**. The page tells you how many transactions it takes and what the
   fees come to. On a real-coin chain you confirm once, for the whole ord.
2. The first transaction pays from your coins into a lock that only your key can spend. It holds the postage and every
   fee still to come. Each next transaction carries about 1,500 bytes of the file and pays the next lock. The last one
   pays the ord's own coin (0.001 of the coin) to your address.
3. Your browser checks every transaction before it signs it. Each one spends only the coin the transaction before it
   made, and pays only your own next lock or your own address. No other coin of yours is spent once the first
   transaction has gone out.

The inscription id is the second transaction's id followed by `i0`.

The chains take only about 25 unconfirmed transactions in a row. So a big file sends 20, waits for a block and carries
on; the page says when it is waiting. Keep the tab open until it finishes. If the tab closes or the arcade restarts,
the inscription is listed under **Not finished yet**: choose the same file again and it carries on where it stopped.
Until then, the coins for the rest wait in your own lock.

## Each chain's rules

Every chain is written the way its own official indexer reads it. Before anything is sent, the page's node reads the
planned transactions back under those rules.

| Chain | Called | Read the way of | Parts | Fees | Empty file |
| --- | --- | --- | --- | --- | --- |
| Dogecoin | Doginal | wonky-ord / ord-dogecoin | 240 bytes | 0.01 DOGE a kB, plus the soft-dust fee on coins under 0.01 | not an inscription |
| Pepecoin | Pepinal | ord-pepecoin | 240 bytes | 0.01 PEP a kB, plus the soft-dust fee | allowed |
| Bellscoin | Bellinal | Nintondo ord (the Doginals kind) | 240 bytes | 0.0001 BEL a kB, no soft-dust fee | not an inscription |
| Luckycoin | Luckinal | Doginals-kind indexers | 240 bytes | 0.01 LKY a kB, plus the soft-dust fee | not an inscription |

Files up to 400 kB are taken.

## Making a collection

Under **Make a collection index**, group your ords into a collection. It uses the same index the Doginal Dogs, the
Doginal Frogs and the Anti Doginal Dogs are published in.

- Ords you inscribe on the page are listed for you. You can add others by their inscription id.
- Give each one a number and, if you like, traits (`Background=Blue, Eyes=Red`). You can also paste a doggy.market
  trait file instead.
- **Inscribe the index** writes the parts and then the root, inscribed by your account on this arcade's own chain.
  Every arcade then shows the collection on the Exchange.

The first index to name an ord owns it, and only the account that published an index can replace it.
