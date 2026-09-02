import Link from "next/link";
import { AddLeague } from "./add-league";

export const leagues = [
  { id: 58005, name: "KK Old Boys", short: "KK Old Boys" },
  { id: 131997, name: "Overall IFE", short: "Overall IFE" },
] as const;

export function resolveLeague(value?: string) {
  const id = Number(value);
  return leagues.find((league) => league.id === id) ?? leagues[0];
}

export function LeagueSwitcher({ selected, pathname }: { selected: number; pathname: string }) {
  return <><div className="league-switcher" aria-label="Select league">{leagues.map((league) => <Link key={league.id} href={`${pathname}?league=${league.id}`} className={selected === league.id ? "active" : ""}><span>{league.short}</span><small>{league.id.toLocaleString()}</small></Link>)}</div><AddLeague /></>;
}
