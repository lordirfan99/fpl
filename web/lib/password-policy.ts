import { createHash, timingSafeEqual } from "node:crypto";

const MAX_PASSWORD_LENGTH = 256;

function digest(value: string) {
  return createHash("sha256").update(value, "utf8").digest();
}

export function passwordMatches(candidate: unknown, configured: string | undefined) {
  if (typeof candidate !== "string" || !configured || candidate.length > MAX_PASSWORD_LENGTH) return false;
  return timingSafeEqual(digest(candidate), digest(configured));
}
