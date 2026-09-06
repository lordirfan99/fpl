import { test, expect } from "@playwright/test";
import { passwordMatches } from "../../lib/password-policy";

test("dashboard password fails closed and compares exact values", () => {
  expect(passwordMatches("correct horse", "correct horse")).toBe(true);
  expect(passwordMatches("Correct horse", "correct horse")).toBe(false);
  expect(passwordMatches("wrong", "correct horse")).toBe(false);
  expect(passwordMatches(undefined, "correct horse")).toBe(false);
  expect(passwordMatches("correct horse", undefined)).toBe(false);
  expect(passwordMatches("x".repeat(257), "x".repeat(257))).toBe(false);
});
