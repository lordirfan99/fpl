"use client";

export default function GlobalError({ reset }: { error: Error; reset: () => void }) {
  return <div className="error-state"><span>DATA CONNECTION</span><h1>We could not load this view.</h1><p>The previous dashboard remains available while the new data service reconnects.</p><button onClick={reset}>Try again</button></div>;
}
