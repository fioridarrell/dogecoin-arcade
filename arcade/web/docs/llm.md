# The LLM service: AI models and pictures on the exchange

Every arcade can sell, or give away, the AI models its operator runs on their own computer: language models (chat,
answers, code) and picture models. Players and apps on **every** arcade can use them, paying the seller directly in
the coins and tokens the exchange already trades. It all lives on the **Exchange → LLMs** tab, which is the first tab
of the Exchange.

## For players: using a model

### The LLMs tab

**Exchange → LLMs** lists every arcade that shares a model, cheapest first. Each card shows:

- the seller (the operator's @name and their arcade's address),
- the model's name and how long a reply it allows,
- its prices, per token, in each coin or token it takes -- **in** is what you send, **out** is what it writes,
- how busy it is right now: **Idle**, **In use**, **Busy** (every slot taken) or **Full** (people waiting), with how
  many replies are running, how many were written in the last hour and how fast it writes (tokens per second),
- **your credit** with that seller.

A model with no prices is **free for everyone**: sign in and use it, nothing to pay.

### Credit and top-ups

You pay a seller by **prepaid credit**:

1. Press **Top up** on the seller's card, pick a coin or token you hold, and enter an amount. The page says roughly how
   much it buys.
2. Your wallet shows the payment and its fee and asks before it signs, like any send.
3. The payment goes **straight to the seller's address**. Their arcade checks it on the chain, that it came from your
   key, and that nobody counted it before, and adds it to your balance there.

Real coins (DOGE, PEP, LTC mainnet) count once the payment is in a block -- about a minute. Test coins count at once.
The seller's arcade keeps your balance, so top up small amounts with sellers you do not know yet.

Each reply is charged after it is written, from the number of tokens the model server reports, and never takes your
balance below zero: a reply is cut short to what your credit still pays for.

### Chat

Press **Chat** on a card. The chat on the Exchange knows DogecoinArcade's own documentation: the seller's arcade adds
the parts of the docs that bear on your question, and those added words are **not charged** -- you pay only for your
own words and the reply. Under each reply you see the tokens in and out and what it cost.

### Pictures

Arcades can also share a **picture model** (on ComfyUI). Pictures are priced in **credits**: a picture made from your
words costs **1 credit**, an edit of a picture you bring (an upload and a download) costs **2 credits**. Press **Make a
picture**, describe it, choose a size, optionally add a picture of yours to change, and **Download** the result.
The same balance with a seller pays for words and pictures.

### Using a model from your own programs (API keys)

The LLMs tab's **Use it from your own apps** gives an **OpenAI-compatible API** on your own arcade:

- Base URL: `https://<your arcade>/v1`
- API key: press **Make a key** (name it, pick which seller and coin it spends). The key starts with `sk-arcade-` and
  is shown **once**; the curl example fills it in for you. Only a scrambled copy is kept; revoke a key at any time.

Any program that takes a base URL and an API key works: chat apps, coding assistants, scripts using the OpenAI
library. Endpoints: `/v1/models`, `/v1/chat/completions` (streaming or not, with tools), `/v1/images/generations` and
`/v1/images/edits`. A key spends one seller's credit in one coin.

### The arcade helper

The **robot** in the lower right corner of every page answers questions about the arcade, using the documentation and
knowing which page and tab you are on. It runs on a model shared with this arcade's operator, so you are not charged;
each person may ask a few dozen times an hour. Its conversation is kept in your browser only. Turn it off on **Your
arcade → Arcade helper**.

### Who you are to a seller, and what is safe

A seller knows you by your @name's address. When you use another arcade's model, your wallet signs, once a month, that
the seller may serve you through your own arcade. A top-up is proved by a signature from the key that paid. Your coins
never leave your wallet except as the top-ups you approve. The seller is trusted to count tokens honestly and to keep
your balance -- that is why top-ups go to the seller and the page suggests starting small.

## For operators: sharing your models

Everything is on **Admin → LLM**.

1. **Find models on this computer** looks at every model server on the machine -- vLLM, llama.cpp, Ollama, LM Studio,
   other OpenAI-compatible servers, and ComfyUI -- and lists their models. Tick the ones to share; the server address
   fills in by itself. A ComfyUI that starts on demand is offered with a **Wake it and look** button.
2. **Sell your model**: the server address (and an API key if the server needs one), the name buyers see, replies at
   once (others wait in line) and the longest reply.
3. **Your prices**: one row per coin or Omni token you accept, each with a price per input token and per output token.
   No prices means **free for everyone**; adding a price makes it paid, removing them all makes it free again.
4. **Share your image model**: ComfyUI's address, a **wake command** for a ComfyUI that sleeps until asked (it runs on
   your machine, so it is set at the node machine only), the largest side, quality steps, pictures at once, and a
   **price per credit**.
5. **Free for other operators**: name other operators (for example `@bigchiefenergy`) who use your models free --
   words and pictures, in chat, apps and API keys -- while everyone else pays your listed prices.
6. Tick **Tick to share on the exchange** and **Save changes**. Your offer reaches every linked arcade within seconds.

**Right now** shows how busy your model is, and **Usage analytics** opens a page with everything about its use:
totals, replies and tokens over time, earnings by coin, by way in (this arcade, API keys, other arcades), by model and
by buyer, and the latest replies. Top-ups arrive in your operator account's Wallet like any other payment, and your own
account always uses your models free.

The **Arcade helper** panel shows which models the robot uses for your players: your own, or one another operator
shares with you free (sign once with your wallet to use it). It never uses a paid model.
