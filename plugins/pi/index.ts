/**
 * BookScout Pi Extension
 *
 * Registers BookScout tools directly into Pi's tool registry.
 * Communicates with the BookScout MCP server via stdio subprocess.
 *
 * Install:
 *   Place in .pi/agent/extensions/bookscout/
 *   Or: npm install @bookscout/pi-extension
 */

import { type ChildProcess,spawn } from "child_process";
import { randomUUID } from "crypto";

// ---------------------------------------------------------------------------
// MCP stdio client — lightweight JSON-RPC over subprocess
// ---------------------------------------------------------------------------

interface MCPRequest {
  jsonrpc: "2.0";
  id: string;
  method: string;
  params?: Record<string, unknown>;
}

interface MCPResponse {
  jsonrpc: "2.0";
  id: string;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

let mcpProcess: ChildProcess | null = null;
let requestId = 0;
const pendingRequests = new Map<string, {
  resolve: (value: unknown) => void;
  reject: (reason: unknown) => void;
}>();
let buffer = "";

function startMCP(): ChildProcess {
  if (mcpProcess) return mcpProcess;

  mcpProcess = spawn("bookscout-mcp", [], {
    stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env },
  });

  mcpProcess.stdout!.on("data", (data: Buffer) => {
    buffer += data.toString();
    // Parse newline-delimited JSON messages
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const response: MCPResponse = JSON.parse(line);
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

  mcpProcess.stderr!.on("data", (data: Buffer) => {
    // MCP servers log to stderr — ignore or forward
    console.error("[bookscout-mcp]", data.toString().trim());
  });

  mcpProcess.on("exit", () => {
    mcpProcess = null;
    // Reject all pending requests
    for (const [id, pending] of pendingRequests) {
      pending.reject(new Error("MCP server exited"));
      pendingRequests.delete(id);
    }
  });

  return mcpProcess;
}

async function callMCP(method: string, params?: Record<string, unknown>): Promise<unknown> {
  const proc = startMCP();
  const id = String(++requestId);

  const request: MCPRequest = {
    jsonrpc: "2.0",
    id,
    method,
    params,
  };

  return new Promise((resolve, reject) => {
    pendingRequests.set(id, { resolve, reject });
    proc.stdin!.write(JSON.stringify(request) + "\n");

    // Timeout after 60 seconds
    setTimeout(() => {
      if (pendingRequests.has(id)) {
        pendingRequests.delete(id);
        reject(new Error("MCP request timed out"));
      }
    }, 60_000);
  });
}

async function callTool(name: string, args: Record<string, unknown>): Promise<string> {
  const result = await callMCP("tools/call", { name, arguments: args });
  // MCP tool results come as content array
  const content = (result as any)?.content;
  if (Array.isArray(content)) {
    return content.map((c: any) => c.text || "").join("\n");
  }
  return String(result);
}

// ---------------------------------------------------------------------------
// Tool definitions — maps BookScout MCP tools to Pi tools
// ---------------------------------------------------------------------------

interface ToolParam {
  name: string;
  type: string;
  description: string;
  required?: boolean;
  enum?: string[];
}

interface ToolDef {
  name: string;
  label: string;
  description: string;
  parameters: {
    type: "object";
    properties: Record<string, ToolParam>;
    required: string[];
  };
  execute: (toolCallId: string, params: Record<string, unknown>) => Promise<string>;
}

const tools: ToolDef[] = [
  {
    name: "bookscout_list_books",
    label: "List Books",
    description: "List all books in the BookScout library with their metadata.",
    parameters: {
      type: "object",
      properties: {},
      required: [],
    },
    execute: async () => callTool("list_books", {}),
  },
  {
    name: "bookscout_get_book",
    label: "Get Book",
    description: "Get detailed information about a specific book.",
    parameters: {
      type: "object",
      properties: {
        book_id: {
          name: "book_id",
          type: "string",
          description: "The book ID to look up.",
          required: true,
        },
      },
      required: ["book_id"],
    },
    execute: async (_, params) => callTool("get_book", params),
  },
  {
    name: "bookscout_search_books",
    label: "Search Books",
    description: "Search books by title or description.",
    parameters: {
      type: "object",
      properties: {
        query: {
          name: "query",
          type: "string",
          description: "Search query string.",
          required: true,
        },
      },
      required: ["query"],
    },
    execute: async (_, params) => callTool("search_books", params),
  },
  {
    name: "bookscout_get_book_nodes",
    label: "Get Book Nodes",
    description: "Get nodes (chapters, sections) for a book.",
    parameters: {
      type: "object",
      properties: {
        book_id: {
          name: "book_id",
          type: "string",
          description: "The book ID.",
          required: true,
        },
        node_type: {
          name: "node_type",
          type: "string",
          description: "Optional filter by node type.",
          required: false,
        },
      },
      required: ["book_id"],
    },
    execute: async (_, params) => callTool("get_book_nodes", params),
  },
  {
    name: "bookscout_get_node_content",
    label: "Get Node Content",
    description: "Get the content of a specific book node (chapter, section).",
    parameters: {
      type: "object",
      properties: {
        node_id: {
          name: "node_id",
          type: "string",
          description: "The node ID to retrieve.",
          required: true,
        },
      },
      required: ["node_id"],
    },
    execute: async (_, params) => callTool("get_node_content", params),
  },
  {
    name: "bookscout_search_summaries",
    label: "Search Summaries",
    description: "Search summary index for a book. Best for conceptual questions and overviews.",
    parameters: {
      type: "object",
      properties: {
        book_id: {
          name: "book_id",
          type: "string",
          description: "The book ID to search in.",
          required: true,
        },
        query: {
          name: "query",
          type: "string",
          description: "Search query string.",
          required: true,
        },
        top_k: {
          name: "top_k",
          type: "integer",
          description: "Number of results to return. Default: 10.",
          required: false,
        },
      },
      required: ["book_id", "query"],
    },
    execute: async (_, params) => callTool("search_summaries", params),
  },
  {
    name: "bookscout_search_chunks",
    label: "Search Chunks",
    description: "Search chunk index for a book using semantic search. Best for finding specific passages.",
    parameters: {
      type: "object",
      properties: {
        book_id: {
          name: "book_id",
          type: "string",
          description: "The book ID to search in.",
          required: true,
        },
        query: {
          name: "query",
          type: "string",
          description: "Search query string.",
          required: true,
        },
        top_k: {
          name: "top_k",
          type: "integer",
          description: "Number of results to return. Default: 10.",
          required: false,
        },
      },
      required: ["book_id", "query"],
    },
    execute: async (_, params) => callTool("search_chunks", params),
  },
  {
    name: "bookscout_search_graph",
    label: "Search Graph",
    description: "Search graph index for a book. Best for relationship queries.",
    parameters: {
      type: "object",
      properties: {
        book_id: {
          name: "book_id",
          type: "string",
          description: "The book ID to search in.",
          required: true,
        },
        query: {
          name: "query",
          type: "string",
          description: "Search query string.",
          required: true,
        },
        top_k: {
          name: "top_k",
          type: "integer",
          description: "Number of results to return. Default: 10.",
          required: false,
        },
      },
      required: ["book_id", "query"],
    },
    execute: async (_, params) => callTool("search_graph", params),
  },
  {
    name: "bookscout_compile_book",
    label: "Compile Book",
    description: "Compile a book from a source file (PDF or EPUB) into BookScout's structured format.",
    parameters: {
      type: "object",
      properties: {
        source_path: {
          name: "source_path",
          type: "string",
          description: "Path to the source file (PDF or EPUB).",
          required: true,
        },
        builder: {
          name: "builder",
          type: "string",
          description: 'Builder type: "rule" (default, fast) or "llm" (higher quality).',
          required: false,
          enum: ["rule", "llm"],
        },
      },
      required: ["source_path"],
    },
    execute: async (_, params) => callTool("compile_book", params),
  },
  {
    name: "bookscout_get_compile_progress",
    label: "Get Compile Progress",
    description: "Get progress of a running compile task.",
    parameters: {
      type: "object",
      properties: {
        task_id: {
          name: "task_id",
          type: "string",
          description: "The task ID returned by compile_book.",
          required: true,
        },
      },
      required: ["task_id"],
    },
    execute: async (_, params) => callTool("get_compile_progress", params),
  },
];

// ---------------------------------------------------------------------------
// Extension entry point
// ---------------------------------------------------------------------------

export default function bookscoutExtension(pi: any): void {
  for (const tool of tools) {
    pi.registerTool({
      name: tool.name,
      label: tool.label,
      description: tool.description,
      parameters: tool.parameters,
      execute: tool.execute,
    });
  }

  console.log("[bookscout] Registered", tools.length, "tools");
}
