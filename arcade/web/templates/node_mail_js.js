/* The node's own identity, for the pages the accounts use (me.html with node_side; 2026-10-19). What messaging.js
 * does in an account's tab -- publish the key, the name, the profile -- the node does with its own wallet, through
 * the routes its old Overview and Address book posted to. */

async function form(path, fields) {
  const body = new URLSearchParams({csrf_token: (window.ARCADE_CSRF || ""), ...fields});
  const r = await fetch(path, {method: "POST", credentials: "same-origin", body, redirect: "follow"});
  if (!r.ok) throw new Error(`refused (${r.status})`);
  return r;
}

async function said() {
  return (await fetch("/node/wallet/state", {cache: "no-store"})).json();
}

export async function identity() {
  const s = await said();
  return {publicKey: s.identity, node: true};
}

export async function contactCode() {
  return (await said()).contact || "—";
}

/** Publish the key (with the name it has, if any): /publish files both in one announcement. */
export async function announce() {
  const s = await said();
  await form("/publish", {tag: s.tag || s.name || ""});
  return {ok: true};
}

/** The node's profile: a picture goes by /profile/picture, a bio and link by /profile/about. */
export async function publishProfile(keys, me, profile) {
  if ("bio" in profile || "url" in profile) {
    await form("/profile/about", {bio: profile.bio || "", url: profile.url || ""});
  }
  if (profile.pfp !== undefined && !("bio" in profile)) {
    await form("/profile/picture", {piece: profile.pfp || ""});
  }
  return {ok: true};
}

export async function inbox() { return []; }

/* The node's Address book (its contacts table), in the shape messaging.js keeps an account's book. */
async function call(path, body) {
  const r = await fetch(path, {method: "POST", credentials: "same-origin",
    headers: {"Content-Type": "application/json", "X-Arcade-Admin": "1"}, body: JSON.stringify(body || {})});
  const said = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(said.detail || `refused (${r.status})`);
  return said;
}
export async function book() {
  try { return ((await (await fetch("/node/contacts", {cache: "no-store"})).json()).book || [])
    .sort((a, b) => a.tag.localeCompare(b.tag)); } catch (e) { return []; }
}
export async function addToBook(tag) { return call("/node/contacts/add", {tag}); }
export async function updateBookEntry(tag, fields) { return call("/node/contacts/update", {tag, ...fields}); }
export async function removeFromBook(tag) { return call("/node/contacts/remove", {tag}); }
export async function findNames(text) {
  const answer = await fetch(`/account/find?q=${encodeURIComponent(text)}`);
  if (!answer.ok) return [];
  return (await answer.json()).matches;
}
export async function lookUp(name) {
  const answer = await fetch(`/account/who/${encodeURIComponent(name)}`);
  const said = await answer.json();
  if (!answer.ok) throw new Error(said.detail || "nobody by that name");
  return said;
}
export async function syncMailbox() { return false; }
