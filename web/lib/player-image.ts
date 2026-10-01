// Single source of truth for player imagery. Cards, pitches and lists call
// getPlayerImage() instead of building CDN URLs themselves.
//
// FPL exposes each player's `photo` as "<opta-code>.jpg" and each team a
// numeric `code`. The headshot lives on the Premier League CDN at a stable
// path; the club shirt then the badge are the graceful fallbacks, and two
// initials are the final fallback that can never fail.

export interface PlayerImageSources {
  /** Player headshot. Null when there is no usable code; may still 404 for
   *  players with no CDN asset (youth players, very new signings). */
  photoUrl: string | null;
  /** Club shirt — first fallback. */
  shirtUrl: string | null;
  /** Club badge — second fallback. */
  badgeUrl: string | null;
  /** Up to two uppercase initials — final fallback. */
  initials: string;
}

const PLAYER_PHOTO_BASE = "https://resources.premierleague.com/premierleague/photos/players/110x140/p";
const SHIRT_BASE = "https://fantasy.premierleague.com/dist/img/shirts/standard/shirt_";
const BADGE_BASE = "https://resources.premierleague.com/premierleague/badges/70/t";

/** Extract the numeric opta code from an FPL `photo` value, or null. */
export function playerPhotoCode(photo?: string | null): string | null {
  if (!photo) return null;
  const code = String(photo).replace(/\.(jpg|jpeg|png|webp)$/i, "").trim();
  return /^\d+$/.test(code) ? code : null;
}

export function playerInitials(name: string): string {
  const parts = name.split(/\s+/).filter(Boolean).slice(0, 2);
  const initials = parts.map((part) => part[0]?.toUpperCase() ?? "").join("");
  return initials || "?";
}

export function getPlayerImage(input: { photo?: string | null; teamCode?: number | null; name: string }): PlayerImageSources {
  const code = playerPhotoCode(input.photo);
  const teamCode = typeof input.teamCode === "number" && input.teamCode > 0 ? input.teamCode : null;
  return {
    photoUrl: code ? `${PLAYER_PHOTO_BASE}${code}.png` : null,
    shirtUrl: teamCode ? `${SHIRT_BASE}${teamCode}-110.webp` : null,
    badgeUrl: teamCode ? `${BADGE_BASE}${teamCode}.png` : null,
    initials: playerInitials(input.name),
  };
}
