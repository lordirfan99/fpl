"use client";

import { useEffect, useState } from "react";

// Transfer chips make the single-transfer recommendation moot. FPL keeps the
// upcoming GW's picks (and therefore `active_chip`) private until the deadline,
// so before then the only source is the manager telling us. `liveActiveChip`
// comes from the API once the deadline passes and always wins.
const STORE_KEY = "fpl-scout-chip-note-v1";
const LABEL: Record<string, string> = { wildcard: "Wildcard", freehit: "Free Hit", bboost: "Bench Boost", "3xc": "Triple Captain" };
const TRANSFER_CHIPS = new Set(["wildcard", "freehit"]);

type SelfReport = { chip: string; gw: number };

export function ChipNotice({ targetGameweek, liveGameweek, liveActiveChip }: { targetGameweek?: number; liveGameweek?: number; liveActiveChip?: string | null }) {
  const [selfReport, setSelfReport] = useState<SelfReport | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setReady(true);
    try {
      const raw = window.localStorage.getItem(STORE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as SelfReport;
      if (targetGameweek != null && parsed.gw < targetGameweek) {
        window.localStorage.removeItem(STORE_KEY); // rolled into a new GW — expired
        return;
      }
      setSelfReport(parsed);
    } catch {
      /* ignore malformed */
    }
  }, [targetGameweek]);

  function record(chip: string | null) {
    if (chip && targetGameweek != null) {
      const next = { chip, gw: targetGameweek };
      setSelfReport(next);
      try { window.localStorage.setItem(STORE_KEY, JSON.stringify(next)); } catch { /* private mode */ }
    } else {
      setSelfReport(null);
      try { window.localStorage.removeItem(STORE_KEY); } catch { /* private mode */ }
    }
  }

  const selfChip = selfReport?.gw === targetGameweek ? selfReport?.chip ?? null : null;
  const chip = (liveActiveChip ?? selfChip)?.toLowerCase() ?? null;
  // A confirmed chip is for the gameweek being played now; a self-report is for
  // the upcoming deadline.
  const chipGameweek = liveActiveChip ? liveGameweek ?? targetGameweek : targetGameweek;
  const gwLabel = chipGameweek != null ? `GW${chipGameweek}` : "this gameweek";

  if (chip) {
    const name = LABEL[chip] ?? chip;
    const transfer = TRANSFER_CHIPS.has(chip);
    return (
      <section className="chip-note">
        <span className="status-dot" />
        <div>
          <strong>🃏 {name} {liveActiveChip ? "played" : "active"} for {gwLabel}</strong>
          <p>
            {transfer && liveActiveChip
              ? <>You rebuilt with unlimited transfers this week. Any single-transfer advice on the dashboard is about the <em>next</em> deadline, not {gwLabel}. Use <a href="/transfers">Transfers &amp; Chips</a> to review the multi-week shape.</>
              : transfer
                ? <>Unlimited free transfers this week, so the single-transfer suggestion below doesn&rsquo;t apply. Use <a href="/transfers">Transfers &amp; Chips</a> for the multi-week plan, or your Telegram bot for the full-squad rebuild.</>
                : <>Your captain and bench choices carry more weight than usual this week; the transfer suggestion still stands.</>}
          </p>
          <p style={{ opacity: 0.75 }}>
            {liveActiveChip
              ? "Confirmed from your live FPL team."
              : <>You marked this. <button type="button" className="chip-undo" onClick={() => record(null)}>Undo</button></>}
          </p>
        </div>
      </section>
    );
  }

  // No chip known. Once the deadline passes the API reports it; before then it
  // can't, so offer a one-tap self-report for the transfer chips only.
  if (!ready || liveActiveChip != null) return null;
  return (
    <div className="chip-ask">
      <span>Already played a chip for {gwLabel}?</span>
      <button type="button" onClick={() => record("wildcard")}>Wildcard</button>
      <button type="button" onClick={() => record("freehit")}>Free Hit</button>
      <button type="button" className="quiet" onClick={() => record(null)}>No / clear</button>
    </div>
  );
}
