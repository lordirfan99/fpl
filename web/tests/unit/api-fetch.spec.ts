import { test, expect } from "@playwright/test";
import { createServer } from "node:http";
import { fetchApi } from "../../lib/api-fetch";

test("timeout also aborts a body stalled after HTTP 200 headers", async () => {
  const server = createServer((_req, res) => { res.writeHead(200, { "Content-Type": "application/json" }); res.write('{"pending":'); });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("No test address");
  try {
    const response = await fetchApi(`http://127.0.0.1:${address.port}`, {}, 150);
    expect(response.status).toBe(200);
    await expect(response.json()).rejects.toThrow();
  } finally {
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});
