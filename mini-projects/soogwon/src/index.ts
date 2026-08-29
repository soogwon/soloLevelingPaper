#!/usr/bin/env node
import { serveStdio } from "@modelcontextprotocol/server/stdio";
import { loadConfig } from "./config.js";
import { StructuredLogger } from "./infrastructure/logger.js";
import { createServer } from "./server.js";

const config = loadConfig();
const logger = new StructuredLogger(config.logLevel);
const handle = serveStdio(() => createServer({ config, logger }), {
  onerror: () => logger.system("mcp_stdio_error", "error"),
});

const shutdown = async (): Promise<void> => {
  await handle.close();
  process.exit(0);
};

process.once("SIGINT", () => void shutdown());
process.once("SIGTERM", () => void shutdown());
logger.system("mcp_stdio_started", "info");
