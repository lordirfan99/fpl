"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTransition, type MouseEvent } from "react";

export type NavTab = { key: string | number; href: string; label: string; sub?: string };

// A row of same-page tabs (league / gameweek switchers). The navigation runs
// inside a transition so the current content stays on screen — dimmed, not
// replaced by a spinner — while the new server render streams in. The target
// is prefetched on hover/focus so a click after that resolves from cache.
export function NavTabs({
  tabs, active, ariaLabel, className = "league-switcher",
}: { tabs: NavTab[]; active: string | number; ariaLabel: string; className?: string }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  const onClick = (event: MouseEvent<HTMLAnchorElement>, href: string) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    startTransition(() => router.push(href, { scroll: false }));
  };

  return (
    <div className={`${className}${pending ? " is-pending" : ""}`} aria-label={ariaLabel} aria-busy={pending || undefined}>
      {tabs.map((tab) => (
        <Link
          key={tab.key}
          href={tab.href}
          prefetch={false}
          className={tab.key === active ? "active" : ""}
          aria-current={tab.key === active ? "page" : undefined}
          onMouseEnter={() => router.prefetch(tab.href)}
          onFocus={() => router.prefetch(tab.href)}
          onClick={(event) => onClick(event, tab.href)}
        >
          <span>{tab.label}</span>
          {tab.sub ? <small>{tab.sub}</small> : null}
        </Link>
      ))}
    </div>
  );
}
