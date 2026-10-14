"use client";

import Link from "next/link";
import { useEffect } from "react";
import { AlertTriangle, ArrowLeft, RefreshCw } from "lucide-react";

// Route-level boundary keeps the sidebar and mobile navigation interactive
// while a backend-backed section is unavailable. Do not echo upstream error
// messages or private URLs into the page.
export default function DashboardError({ reset }: { error: Error; reset: () => void }) {
  useEffect(() => {
    // The user can recover with the navigation even if a single route fails.
  }, []);
  return (
    <div className="page-stack" role="alert">
      <section className="surface" style={{ maxWidth: 720, margin: "32px auto", width: "100%" }}>
        <span className="eyebrow">DATA SERVICE UNAVAILABLE</span>
        <h1 style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
          <AlertTriangle size={28} aria-hidden="true" /> This view could not load
        </h1>
        <p>The latest FPL snapshot or upstream API did not respond. Other sections of the dashboard should remain accessible. No transfer, captain or chip recommendation has been inferred.</p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 24 }}>
          <button type="button" onClick={reset}><RefreshCw size={16} aria-hidden="true" /> Retry this view</button>
          <Link href="/this-week"><ArrowLeft size={16} aria-hidden="true" /> This Week</Link>
          <Link href="/settings">Settings</Link>
        </div>
      </section>
    </div>
  );
}
