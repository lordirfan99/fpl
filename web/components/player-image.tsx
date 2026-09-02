"use client";

import Image from "next/image";
import { useState } from "react";

export function PlayerImage({ photo, badgeCode, name }: { photo?: string; badgeCode?: number; name: string }) {
  const [photoFailed, setPhotoFailed] = useState(false);
  const [shirtFailed, setShirtFailed] = useState(false);
  const cleaned = photo?.replace(/\.(jpg|png)$/i, "");
  const playerUrl = cleaned ? `https://resources.premierleague.com/premierleague/photos/players/110x140/p${cleaned}.png` : null;
  const shirtUrl = badgeCode ? `https://fantasy.premierleague.com/dist/img/shirts/standard/shirt_${badgeCode}-110.webp` : null;
  const badgeUrl = badgeCode ? `https://resources.premierleague.com/premierleague/badges/70/t${badgeCode}.png` : null;
  if (playerUrl && !photoFailed) return <Image unoptimized src={playerUrl} alt="" fill sizes="84px" onError={() => setPhotoFailed(true)} />;
  if (shirtUrl && !shirtFailed) return <span className="shirt-fallback"><Image unoptimized src={shirtUrl} alt="" width={66} height={84} onError={() => setShirtFailed(true)} /></span>;
  if (badgeUrl) return <span className="badge-fallback"><Image unoptimized src={badgeUrl} alt="" width={46} height={46} /></span>;
  return <span className="initial-fallback">{name.split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase()}</span>;
}
