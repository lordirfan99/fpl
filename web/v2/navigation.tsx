"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const sections = [
  ["/v2", "Overview"], ["/v2/team", "My Team"], ["/v2/league", "League"],
  ["/v2/elite", "Elite"], ["/v2/players", "Players"], ["/v2/compare", "Compare"],
  ["/v2/planner", "Planner"], ["/v2/journal", "Journal"],
];

export function Navigation() {
  const pathname = usePathname();
  return <nav aria-label="FPL V2 navigation">{sections.map(([href, label]) =>
    <Link key={href} href={href} aria-current={pathname === href ? "page" : undefined}>{label}</Link>
  )}</nav>;
}
