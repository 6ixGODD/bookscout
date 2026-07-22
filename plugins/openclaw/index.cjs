/**
 * BookScout OpenClaw Extension
 *
 * Registers BookScout tools into OpenClaw's tool registry.
 * Communicates with the BookScout MCP server via stdio subprocess.
 *
 * Install:
 *   openbot extension install bookscout-openclaw-extension
 *   Or: openbot extension install ./path/to/this/directory
 */

const { spawn } = require("child_process");

// ---------------------------------------------------------------------------
// MCP stdio client — lightweight JSON-RPC over subprocess
// ---------------------------------------------------------------------------

let mcpProcess = null;
let requestId = 0;
const pendingRequests = new Map();
let buffer = "";

function startMCP() {
  if (mcpProcess) return mcpProcess;

  mcpProcess = spawn("bookscout-mcp", [], {
    stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env },
  });

  mcpProcess.stdout.on("data", (data) => {
    buffer += data.toString();
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const response = JSON.parse(line);
        const pending = pendingRequests.get(response.id);
        if (pending) {
          pendingRequests.delete(response.id);
          if (response.error) {
            pending.reject(new Error(response.error.message));
          } else {
            pending.resolve(response.result);
          }
        }
      } catch {
        // Ignore non-JSON lines (server logs)
      }
    }
  });

  mcpProcess.stderr.on("data", (data) => {
    console.error("[bookscout-mcp]", data.toString().trim());
  });

  mcpProcess.on("exit", () => {
    mcpProcess = null;
    for (const [id, pending] of pendingRequests) {
      pending.reject(new Error("MCP server exited"));
      pendingRequests.delete(id);
    }
  });

  return mcpProcess;
}

async function callMCP(method, params) {
  const proc = startMCP();
  const id = String(++requestId);

  const request = { jsonrpc: "2.0", id, method, params };

  return new Promise((resolve, reject) => {
    pendingRequests.set(id, { resolve, reject });
    proc.stdin.write(JSON.stringify(request) + "\n");

    setTimeout(() => {
      if (pendingRequests.has(id)) {
        pendingRequests.delete(id);
        reject(new Error("MCP request timed out"));
      }
    }, 60_000);
  });
}

async function callTool(name, args) {
  const result = await callMCP("tools/call", { name, arguments: args });
  const content = result?.content;
  if (Array.isArray(content)) {
    return content.map((c) => c.text || "").join("\n");
  }
  return String(result);
}

// ---------------------------------------------------------------------------
// Tool definitions
// ---------------------------------------------------------------------------

const TOOLS = [
  {
    name: "bookscout_list_books",
    description: "List all books in the BookScout library with their metadata.",
    parameters: { type: "object", properties: {}, required: [] },
    execute: async () => callTool("list_books", {}),
  },
  {
    name: "bookscout_get_book",
    description: "Get detailed information about a specific book.",
    parameters: {
      type: "object",
      properties: {
        book_id: { type: "string", description: "The book ID to look up." },
      },
      required: ["book_id"],
    },
    execute: async (_, params) => callTool("get_book", params),
  },
  {
    name: "bookscout_search_books",
    description: "Search books by title or description.",
    parameters: {
      type: "object",
      properties: {
        query: { type: "string", description: "Search query string." },
      },
      required: ["query"],
    },
    execute: async (_, params) => callTool("search_books", params),
  },
  {
    name: "bookscout_get_book_nodes",
    description: "Get nodes (chapters, sections) for a book.",
    parameters: {
      type: "object",
      properties: {
        book_id: { type: "string", description: "The book ID." },
        node_type: { type: "string", description: "Optional filter by node type." },
      },
      required: ["book_id"],
    },
    execute: async (_, params) => callTool("get_book_nodes", params),
  },
  {
    name: "bookscout_get_node_content",
    description: "Get the content of a specific book node (chapter, section).",
    parameters: {
      type: "object",
      properties: {
        node_id: { type: "string", description: "The node ID to retrieve." },
      },
      required: ["node_id"],
    },
    execute: async (_, params) => callTool("get_node_content", params),
  },
  {
    name: "bookscout_search_summaries",
    description: "Search summary index for a book. Best for conceptual questions.",
    parameters: {
      type: "object",
      properties: {
        book_id: { type: "string", description: "The book ID to search in." },
        query: { type: "string", description: "Search query string." },
        top_k: { type: "integer", description: "Number of results. Default: 10." },
      },
      required: ["book_id", "query"],
    },
    execute: async (_, params) => callTool("search_summaries", params),
  },
  {
    name: "bookscout_search_chunks",
    description: "Search chunk index for a book. Best for finding specific passages.",
    parameters: {
      type: "object",
      properties: {
        book_id: { type: "string", description: "The book ID to search in." },
        query: { type: "string", description: "Search query string." },
        top_k: { type: "integer", description: "Number of results. Default: 10." },
      },
      required: ["book_id", "query"],
    },
    execute: async (_, params) => callTool("search_chunks", params),
  },
  {
    name: "bookscout_search_graph",
    description: "Search graph index for a book. Best for relationship queries.",
    parameters: {
      type: "object",
      properties: {
        book_id: { type: "string", description: "The book ID to search in." },
        query: { type: "string", description: "Search query string." },
        top_k: { type: "integer", description: "Number of results. Default: 10." },
      },
      required: ["book_id", "query"],
    },
    execute: async (_, params) => callTool("search_graph", params),
  },
  {
    name: "bookscout_compile_book",
    description: "Compile a book from a source file (PDF or EPUB).",
    parameters: {
      type: "object",
      properties: {
        source_path: { type: "string", description: "Path to the source file." },
        builder: {
          type: "string",
          description: 'Builder type: "rule" (default) or "llm".',
          enum: ["rule", "llm"],
        },
      },
      required: ["source_path"],
    },
    execute: async (_, params) => callTool("compile_book", params),
  },
  {
    name: "bookscout_get_compile_progress",
    description: "Get progress of a running compile task.",
    parameters: {
      type: "object",
      properties: {
        task_id: { type: "string", description: "The task ID returned by compile_book." },
      },
      required: ["task_id"],
    },
    execute: async (_, params) => callTool("get_compile_progress", params),
  },
];

// ---------------------------------------------------------------------------
// Extension entry point — matches OpenClaw's (pi) => void convention
// ---------------------------------------------------------------------------

module.exports = function bookscoutExtension(pi) {
  for (const tool of TOOLS) {
    pi.registerTool({
      name: tool.name,
      description: tool.description,
      parameters: tool.parameters,
      execute: tool.execute,
    });
  }

  console.log("[bookscout] Registered", TOOLS.length, "tools");
};
