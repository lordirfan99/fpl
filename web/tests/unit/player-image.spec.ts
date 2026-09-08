import { test, expect } from "@playwright/test";
import { getPlayerImage, playerInitials, playerPhotoCode } from "../../lib/player-image";

test("playerPhotoCode strips the extension and rejects non-numeric values", () => {
  expect(playerPhotoCode("154561.jpg")).toBe("154561");
  expect(playerPhotoCode("154561.png")).toBe("154561");
  expect(playerPhotoCode("154561")).toBe("154561");
  expect(playerPhotoCode("")).toBeNull();
  expect(playerPhotoCode(undefined)).toBeNull();
  expect(playerPhotoCode("missing.jpg")).toBeNull();
});

test("playerInitials takes up to two initials and always returns something", () => {
  expect(playerInitials("Bruno Fernandes")).toBe("BF");
  expect(playerInitials("Rodri")).toBe("R");
  expect(playerInitials("Alexander-Arnold Trent James")).toBe("AT");
  expect(playerInitials("   ")).toBe("?");
});

test("getPlayerImage builds the CDN headshot, shirt and badge from stable ids", () => {
  const sources = getPlayerImage({ photo: "154561.jpg", teamCode: 3, name: "David Raya" });
  expect(sources.photoUrl).toBe("https://resources.premierleague.com/premierleague/photos/players/110x140/p154561.png");
  expect(sources.shirtUrl).toBe("https://fantasy.premierleague.com/dist/img/shirts/standard/shirt_3-110.webp");
  expect(sources.badgeUrl).toBe("https://resources.premierleague.com/premierleague/badges/70/t3.png");
  expect(sources.initials).toBe("DR");
});

test("getPlayerImage degrades: no photo -> shirt/badge only; no team -> initials only", () => {
  const noPhoto = getPlayerImage({ photo: undefined, teamCode: 7, name: "New Signing" });
  expect(noPhoto.photoUrl).toBeNull();
  expect(noPhoto.shirtUrl).toContain("shirt_7-110.webp");

  const nothing = getPlayerImage({ photo: null, teamCode: 0, name: "Ghost Player" });
  expect(nothing.photoUrl).toBeNull();
  expect(nothing.shirtUrl).toBeNull();
  expect(nothing.badgeUrl).toBeNull();
  expect(nothing.initials).toBe("GP");
});
