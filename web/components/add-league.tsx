"use client";

import { useState } from "react";

export function AddLeague() {
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [copied, setCopied] = useState(false);

  async function request(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!/^\d+$/.test(id)) return;
    const snippet = JSON.stringify({ id: Number(id), name: name.trim() || `League ${id}` });
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 4000);
    } catch {
      setCopied(false);
    }
  }

  return <form className="add-league" aria-label="Request league tracking" onSubmit={request}>
    <label className="sr-only" htmlFor="league-id">FPL league ID</label>
    <input id="league-id" inputMode="numeric" value={id} onChange={(event) => setId(event.target.value.replace(/\D/g, ""))} placeholder="League ID" aria-describedby="league-help" required />
    <label className="sr-only" htmlFor="league-name">Optional league name</label>
    <input id="league-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Optional name" />
    <button type="submit" disabled={!id}>{copied ? "Copied" : "Copy entry"}</button>
    <small id="league-help">Tracked leagues are managed in <code>data/league_registry.json</code>. This copies a registry entry to your clipboard to add there — nothing is sent anywhere.</small>
  </form>;
}
