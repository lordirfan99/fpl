"use client";

import { useEffect, useMemo, useState } from "react";

type SquadPick = { element: number; name: string; position: string; cost: number };
type Player = { id: number; web_name: string; element_type: number; now_cost: number; ep_next: string; team: number; status: string };
type Team = { id: number; short_name: string };
const positionIds: Record<string, number> = { GKP: 1, DEF: 2, MID: 3, FWD: 4 };

export function TransferDraft({ squad, players, teams, gameweek }: { squad: SquadPick[]; players: Player[]; teams: Team[]; gameweek: number }) {
  const [outId, setOutId] = useState(String(squad[0]?.element ?? ""));
  const [inId, setInId] = useState("");
  const [saved, setSaved] = useState(false);
  const selectedOut = squad.find((pick) => String(pick.element) === outId);
  const candidates = useMemo(() => players.filter((player) => player.element_type === positionIds[selectedOut?.position ?? ""] && player.id !== selectedOut?.element).toSorted((a, b) => Number(b.ep_next) - Number(a.ep_next)).slice(0, 40), [players, selectedOut]);
  const selectedIn = candidates.find((player) => String(player.id) === inId);
  const selectedOutProjection = players.find((player) => player.id === selectedOut?.element);
  const teamNames = useMemo(() => new Map(teams.map((team) => [team.id, team.short_name])), [teams]);
  const key = `fpl-transfer-draft-gw${gameweek}`;
  useEffect(() => { const stored = window.localStorage.getItem(key); if (stored) { try { const draft = JSON.parse(stored) as { outId?: string; inId?: string }; if (draft.outId) setOutId(draft.outId); if (draft.inId) setInId(draft.inId); } catch { window.localStorage.removeItem(key); } } }, [key]);
  useEffect(() => { setInId(""); }, [outId]);
  const priceDelta = selectedOut && selectedIn ? selectedIn.now_cost / 10 - selectedOut.cost : null;
  const pointDelta = selectedOutProjection && selectedIn ? Number(selectedIn.ep_next) - Number(selectedOutProjection.ep_next) : null;
  function saveDraft() { window.localStorage.setItem(key, JSON.stringify({ outId, inId })); setSaved(true); }
  function clearDraft() { window.localStorage.removeItem(key); setOutId(String(squad[0]?.element ?? "")); setInId(""); setSaved(false); }
  return <section className="surface transfer-draft"><div className="section-heading"><div><span>YOUR DRAFT</span><h2>Test a transfer scenario</h2><p>This stays on this device and never submits an FPL transfer. The xPts change is a gross one-week comparison before hits, multi-week value or selling-price validation.</p></div><span className="section-chip">GW{gameweek} research</span></div><div className="draft-controls"><label>Transfer out<select value={outId} onChange={(event) => setOutId(event.target.value)}>{squad.map((pick) => <option key={pick.element} value={pick.element}>{pick.name} · {pick.position} · £{pick.cost.toFixed(1)}m</option>)}</select></label><label>Transfer in<select value={inId} onChange={(event) => setInId(event.target.value)}><option value="">Choose same-position player</option>{candidates.map((player) => <option key={player.id} value={player.id}>{player.web_name} · {teamNames.get(player.team) ?? "—"} · £{(player.now_cost / 10).toFixed(1)}m · {Number(player.ep_next).toFixed(1)} xPts</option>)}</select></label></div>{selectedOut && selectedIn ? <div className="draft-result"><div><span>Draft move</span><strong>{selectedOut.name} → {selectedIn.web_name}</strong></div><div><span>Budget change</span><strong className={priceDelta != null && priceDelta > 0 ? "negative-text" : "positive-text"}>{priceDelta != null && priceDelta > 0 ? "+" : ""}£{priceDelta?.toFixed(1)}m</strong></div><div><span>Gross xPts change</span><strong className={pointDelta != null && pointDelta >= 0 ? "positive-text" : "negative-text"}>{pointDelta != null && pointDelta > 0 ? "+" : ""}{pointDelta?.toFixed(1)}</strong></div><button type="button" onClick={saveDraft}>{saved ? "Saved on this device" : "Save draft"}</button><button type="button" className="quiet" onClick={clearDraft}>Clear</button></div> : <p className="draft-empty">Choose a same-position replacement to compare price and next-round xPts.</p>}</section>;
}
