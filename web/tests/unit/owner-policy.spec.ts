import { test, expect } from "@playwright/test";
import { ownerEmail, verifiedGoogleOwner } from "../../lib/owner-policy";

test("only explicitly allowlisted, verified Google owner is accepted", () => {
  const owner = "owner@example.com";
  expect(verifiedGoogleOwner("google", true, owner, owner)).toBe(true);
  expect(verifiedGoogleOwner("google", false, owner, owner)).toBe(false);
  expect(verifiedGoogleOwner("google", "true", owner, owner)).toBe(false);
  expect(verifiedGoogleOwner("other", true, owner, owner)).toBe(false);
  expect(verifiedGoogleOwner("google", true, "attacker@example.com", owner)).toBe(false);
  expect(verifiedGoogleOwner("google", true, owner, undefined)).toBe(false);
  expect(ownerEmail(owner, " ")).toBe(false);
  expect(ownerEmail(undefined, owner)).toBe(false);
});
