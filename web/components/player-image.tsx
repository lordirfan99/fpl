"use client";

import Image from "next/image";
import { useState } from "react";
import { getPlayerImage } from "@/lib/player-image";

// Pitch cards are always near the top of their page and there are only ~15,
// so load eagerly: `loading="lazy"` can stall indefinitely if the card mounts
// inside a briefly-hidden (streaming) subtree and its IntersectionObserver
// never fires. `unoptimized` keeps this off the Netlify image optimiser.
export function PlayerImage({ photo, badgeCode, name }: { photo?: string; badgeCode?: number; name: string }) {
  const [photoFailed, setPhotoFailed] = useState(false);
  const [shirtFailed, setShirtFailed] = useState(false);
  const { photoUrl, shirtUrl, badgeUrl, initials } = getPlayerImage({ photo, teamCode: badgeCode, name });

  if (photoUrl && !photoFailed) {
    return <Image unoptimized loading="eager" src={photoUrl} alt="" fill sizes="84px" onError={() => setPhotoFailed(true)} />;
  }
  if (shirtUrl && !shirtFailed) {
    return <span className="shirt-fallback"><Image unoptimized loading="eager" src={shirtUrl} alt="" width={66} height={84} onError={() => setShirtFailed(true)} /></span>;
  }
  if (badgeUrl) {
    return <span className="badge-fallback"><Image unoptimized loading="eager" src={badgeUrl} alt="" width={46} height={46} /></span>;
  }
  return <span className="initial-fallback">{initials}</span>;
}
