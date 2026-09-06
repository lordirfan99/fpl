// Shown only while a route segment is still streaming. It mirrors the common
// dashboard shape (header, metric row, one large surface) so navigation does
// not flash an empty screen or a full-page spinner, and nothing below shifts
// when the real content arrives.
export default function Loading() {
  return (
    <div className="page-stack" aria-busy="true" aria-label="Loading">
      <div className="skeleton-header">
        <span className="skeleton skeleton-line sk-eyebrow" />
        <span className="skeleton skeleton-line sk-title" />
        <span className="skeleton skeleton-line sk-sub" />
      </div>
      <div className="metric-grid">
        {Array.from({ length: 4 }).map((_, index) => (
          <div className="metric-card" key={index}>
            <span className="skeleton skeleton-line sk-eyebrow" />
            <span className="skeleton skeleton-line sk-metric" />
            <span className="skeleton skeleton-line sk-sub" />
          </div>
        ))}
      </div>
      <div className="surface">
        <span className="skeleton skeleton-line sk-eyebrow" />
        <span className="skeleton skeleton-block" />
      </div>
    </div>
  );
}
