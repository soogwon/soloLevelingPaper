#!/usr/bin/env node
import { serveStdio } from "@modelcontextprotocol/server/stdio";
import { createServer } from "./server.js";

const handle = serveStdio(() => createServer(), {
  onerror: (error) => console.error("MCP stdio 오류:", error.message),
});

const shutdown = async (): Promise<void> => {
  await handle.close();
  process.exit(0);
};

process.once("SIGINT", () => void shutdown());
process.once("SIGTERM", () => void shutdown());
console.error("paper-concept-path-mcp가 stdio에서 실행 중입니다.");
