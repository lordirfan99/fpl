"use client";

export default function GlobalError({ reset }: { error: Error; reset: () => void }) {
  return <div className="error-state" role="alert"><span>DATA CONNECTION</span><h1>We could not load this view.</h1><p>The data service did not respond in time. Try again, or return to This Week to check the available sections.</p><button onClick={reset}>Try again</button><a href="/this-week">Open This Week</a></div>;
}
