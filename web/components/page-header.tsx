import { Clock3 } from "lucide-react";

export function PageHeader({ eyebrow, title, description, updated, freshnessLabel }: { eyebrow: string; title: string; description: string; updated?: string; freshnessLabel?: string }) {
  return <header className="page-header"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div><div className="freshness"><Clock3 size={15} /><span>{freshnessLabel ?? (updated ? `Updated ${updated}` : "Live snapshot")}</span></div></header>;
}
