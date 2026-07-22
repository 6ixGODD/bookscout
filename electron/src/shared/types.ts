/** Shared TypeScript types for the BookScout Electron client. */

// ---------------------------------------------------------------------------
// WebSocket protocol types — mirrors Python ReplServer protocol exactly
// ---------------------------------------------------------------------------

/** Client → Server request types. */
export type RequestType =
  | "chat"
  | "list_books"
  | "compile"
  | "build_indexes"
  | "get_task_progress"
  | "list_sessions"
  | "create_session"
  | "delete_session"
  | "rename_session"
  | "load_messages"
  | "shutdown";

/** Server → Client response types. */
export type ResponseType =
  | "stream_chunk"
  | "chat_done"
  | "books_listed"
  | "task_started"
  | "task_progress"
  | "sessions_listed"
  | "session_created"
  | "session_deleted"
  | "session_renamed"
  | "messages_loaded"
  | "error"
  | "shutdown_ack";

/** Stream chunk kinds — mirrors Python StreamChunk.kind. */
export type ChunkKind = "text" | "tool_call" | "tool_result" | "status" | "done";

/** A request sent from the client to the server. */
export interface WsRequest {
  type: RequestType;
  request_id: string;
  session_id?: string;
  user_input?: string;
  source_path?: string;
  book_id?: string;
  index_types?: string[];
  task_id?: string;
  name?: string;
  kind?: string;
}

/** A response received from the server. */
export interface WsResponse {
  type: ResponseType;
  request_id: string;
  [key: string]: unknown;
}

/** A stream chunk from the server. */
export interface StreamChunkMessage {
  type: "stream_chunk";
  request_id: string;
  kind: ChunkKind;
  data: unknown;
}

// ---------------------------------------------------------------------------
// Domain types
// ---------------------------------------------------------------------------

export interface Book {
  id: string;
  title: string;
  author: string;
  content_path: string;
  checksum: string;
}

export interface Session {
  session_id: string;
  name: string;
  kind: string;
  created_at: number;
  updated_at: number;
  turn_count: number;
  status: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ToolCallData {
  tool_name: string;
  call_id: string;
}

export interface ToolResultData {
  tool_name: string;
  call_id: string;
  summary?: string;
  retrieval_stats?: Record<string, unknown>;
  arguments?: Record<string, unknown>;
  result_text?: string;
}

export interface StatusData {
  phase: string;
  agent?: string;
  tool_name?: string;
  error?: string;
  attempt?: number;
  max_retries?: number;
}

// ---------------------------------------------------------------------------
// UI state types
// ---------------------------------------------------------------------------

/** A single message block in the chat — either user text, assistant text,
 *  a tool call card, or a status indicator. */
export type MessageBlock =
  | { id: string; type: "user"; content: string }
  | { id: string; type: "assistant"; content: string; streaming: boolean }
  | { id: string; type: "tool_call"; data: ToolCallData; result?: ToolResultData }
  | { id: string; type: "status"; data: StatusData };
