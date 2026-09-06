import { AddLeague } from "./add-league";
import { NavTabs } from "./nav-tabs";

export const leagues = [
  { id: 58005, name: "KK Old Boys", short: "KK Old Boys" },
  { id: 131997, name: "Overall IFE", short: "Overall IFE" },
] as const;

export function resolveLeague(value?: string) {
  const id = Number(value);
  return leagues.find((league) => league.id === id) ?? leagues[0];
}

export function LeagueSwitcher({ selected, pathname }: { selected: number; pathname: string }) {
  return (
    <>
      <NavTabs
        ariaLabel="Select league"
        active={selected}
        tabs={leagues.map((league) => ({
          key: league.id,
          href: `${pathname}?league=${league.id}`,
          label: league.short,
          sub: league.id.toLocaleString(),
        }))}
      />
      <AddLeague />
    </>
  );
}
