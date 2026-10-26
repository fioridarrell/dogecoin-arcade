# The .mesh web: pages for the T-Deck

A small web for the LoRa mesh, made the way the web was in 1990: plain text, headings, lists and links, nothing else.
Every page belongs to a `.mesh` name, and the wallet holding the name decides what its page says. A DogecoinArcade
T-Deck reads pages in the **Mesh Browser** app (from its App Store), over WiFi or by radio.

## A name

A `.mesh` name is an ordinary name inscription on **Pepecoin mainnet**:

```
{"p":"dns","op":"reg","name":"yours.mesh"}
```

Claim one from **Creator → Names**. The first claim of a name owns it, and whoever holds that inscription holds the
name: send it to someone and the name goes with it.

## A page

A page is another inscription on Pepecoin mainnet, made to the address that holds the name:

```
{"p":"mesh","name":"yours.mesh","pages":{"/":"# Welcome\n...","/about":"..."}}
```

- `pages` holds the whole site: `/` is the front page; other paths are short words (`/about`, `/notes/today`) of
  lowercase letters, digits, `.`, `_` and `-`, up to 24 of them.
- The whole inscription is at most **3,000 bytes**, so every page loads over LoRa too.
- **Which page counts:** the newest page inscribed to the address that holds the name *now*. Publish again to change
  your site. If the name is sold, your pages stop counting, and the new holder's pages count instead.

**Creator → Names → Publish a page** writes this for you, with a preview at the T-Deck's size.

## MeshText

Each page is plain text, one line at a time:

| Line | Shows as |
|---|---|
| `# Title` | a big heading (the first one is the page's title) |
| `## Section`, `### Small` | smaller headings |
| `* item` | a list item |
| `> words` | a quote |
| `=> other.mesh/path Label` | a link to another site; `=> /path Label` is a page of this site |
| ```` ``` ```` | starts or ends preformatted text, kept exactly (ASCII art) |
| anything else | a paragraph line |

There are no pictures, colours, fonts or scripts. The T-Deck draws every page in its own font on a 320 × 240 screen.
Short lines read best.

```
# Me-Seeks
Hello from the T-Deck web!

## Around here
=> /about About me
=> arcade.mesh The arcade's own site

* Ask me anything over LoRa.
```

## Reading pages

- **On a T-Deck:** the Mesh Browser asks a gateway, over WiFi or LoRa. A page it has already read comes from its own
  memory unless the site changed. Any site can be set as its homepage.
- **In a browser:** `https://app.dogecoinarcade.com/meshweb/yours.mesh` shows a page at the T-Deck's size.
  `/meshweb` lists every site.
- **For programs:** `GET /r/mesh` is the directory (every name with a page, newest first);
  `GET /r/mesh/yours.mesh/about` is one page as JSON: `body` (its MeshText), `title`, `paths`, `page` (the
  inscription), `holder`. Add `?have=<page>` and an unchanged site answers `{"unchanged": true}` without its body.
