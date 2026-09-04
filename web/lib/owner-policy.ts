export function ownerEmail(email: string | null | undefined, allowed: string | undefined) {
  return !!allowed?.trim() && typeof email === "string" && email.trim().toLowerCase() === allowed.trim().toLowerCase();
}
export function verifiedGoogleOwner(provider: unknown, verified: unknown, email: string | null | undefined, allowed: string | undefined) {
  return provider === "google" && verified === true && ownerEmail(email, allowed);
}
