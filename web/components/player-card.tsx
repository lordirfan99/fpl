import type { Bootstrap, Pick } from "@/lib/types";
import { PlayerImage } from "./player-image";

export function PlayerCard({ pick, bootstrap, meta }: { pick: Pick; bootstrap: Bootstrap; meta: string }) {
  const player = bootstrap.elements.find((item) => item.id === pick.element);
  const team = bootstrap.teams.find((item) => item.id === player?.team);
  return <div className="player-card">
    <div className="player-art"><PlayerImage photo={player?.photo} badgeCode={team?.code} name={pick.name} /></div>
    {pick.is_captain && <span className="armband">C</span>}{pick.is_vice_captain && <span className="armband vice">V</span>}
    <strong title={pick.name}>{pick.name}</strong><small>{meta}</small>
  </div>;
}
