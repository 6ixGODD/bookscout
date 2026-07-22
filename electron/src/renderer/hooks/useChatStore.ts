/**
 * Chat store — Zustand state management for the BookScout chat.
 *
 * Manages sessions, message blocks, streaming state, and
 * dispatches WebSocket requests.
 */

import type {
  Book,
  ChatMessage,
  MessageBlock,
  Session,
  StatusData,
  StreamChunkMessage,
  ToolCallData,
  ToolResultData,
  WsResponse,
} from "@shared/types";
import { create } from "zustand";

import { getWsClient } from "../api/ws-client";

// ---------------------------------------------------------------------------
// Store types
// ---------------------------------------------------------------------------

interface ChatState {
  // Connection
  port: number;
  connected: boolean;

  // Sessions
  sessions: Session[];
  activeSessionId: string | null;

  // Messages
  messageBlocks: MessageBlock[];
  historicalMessages: ChatMessage[];

  // Streaming
  streaming: boolean;
  streamingRequestId: string | null;

  // Books
  books: Book[];

  // Actions
  setPort: (port: number) => void;
  setConnected: (connected: boolean) => void;
  loadSessions: () => void;
  createSession: (name?: string) => void;
  selectSession: (sessionId: string) => void;
  deleteSession: (sessionId: string) => void;
  sendMessage: (text: string) => void;
  loadBooks: () => void;
  handleWsMessage: (message: WsResponse) => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let blockIdCounter = 0;
function nextBlockId(): string {
  return `blk_${++blockIdCounter}`;
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useChatStore = create<ChatState>((set, get) => ({
  port: 18732,
  connected: false,
  sessions: [],
  activeSessionId: null,
  messageBlocks: [],
  historicalMessages: [],
  streaming: false,
  streamingRequestId: null,
  books: [],

  setPort: (port) => set({ port }),

  setConnected: (connected) => set({ connected }),

  loadSessions: () => {
    const client = getWsClient(get().port);
    client.send("list_sessions");
  },

  createSession: (name) => {
    const client = getWsClient(get().port);
    client.send("create_session", { name: name || "New Session", kind: "chat" });
  },

  selectSession: (sessionId) => {
    set({ activeSessionId: sessionId, messageBlocks: [], historicalMessages: [] });
    const client = getWsClient(get().port);
    client.send("load_messages", { session_id: sessionId });
  },

  deleteSession: (sessionId) => {
    const client = getWsClient(get().port);
    client.send("delete_session", { session_id: sessionId });
  },

  sendMessage: (text) => {
    const state = get();
    if (state.streaming) return;

    const client = getWsClient(state.port);
    const sessionId = state.activeSessionId || undefined;

    // Add user message block immediately.
    const userBlock: MessageBlock = {
      id: nextBlockId(),
      type: "user",
      content: text,
    };

    // Prepare assistant block for streaming.
    const assistantBlock: MessageBlock = {
      id: nextBlockId(),
      type: "assistant",
      content: "",
      streaming: true,
    };

    const reqId = client.send("chat", {
      session_id: sessionId,
      user_input: text,
    });

    set({
      messageBlocks: [...state.messageBlocks, userBlock, assistantBlock],
      streaming: true,
      streamingRequestId: reqId,
    });
  },

  loadBooks: () => {
    const client = getWsClient(get().port);
    client.send("list_books");
  },

  handleWsMessage: (message) => {
    const state = get();

    switch (message.type) {
      case "stream_chunk": {
        const chunk = message as unknown as StreamChunkMessage;
        // Only process chunks for the active streaming request.
        if (chunk.request_id !== state.streamingRequestId) return;

        const blocks = [...state.messageBlocks];

        if (chunk.kind === "text") {
          // Append text to the last assistant block.
          const lastAssistantIdx = blocks.length - 1;
          if (lastAssistantIdx >= 0 && blocks[lastAssistantIdx].type === "assistant") {
            const block = { ...blocks[lastAssistantIdx] } as MessageBlock & { type: "assistant" };
            block.content += String(chunk.data);
            blocks[lastAssistantIdx] = block;
          }
        } else if (chunk.kind === "tool_call") {
          const data = chunk.data as ToolCallData;
          blocks.push({
            id: nextBlockId(),
            type: "tool_call",
            data,
          });
        } else if (chunk.kind === "tool_result") {
          const data = chunk.data as ToolResultData;
          // Find the matching tool_call block and attach the result.
          const callIdx = blocks.findIndex(
            (b) => b.type === "tool_call" && (b as any).data?.call_id === data.call_id
          );
          if (callIdx >= 0) {
            const block = { ...blocks[callIdx] } as MessageBlock & { type: "tool_call" };
            block.result = data;
            blocks[callIdx] = block;
          }
        } else if (chunk.kind === "status") {
          const data = chunk.data as StatusData;
          if (data.phase === "preparing" || data.phase === "running_agent") {
            blocks.push({
              id: nextBlockId(),
              type: "status",
              data,
            });
          } else if (data.phase === "tool_executed" || data.phase === "auto_compacted") {
            // Remove the last status block (phase completed).
            const lastIdx = blocks.length - 1;
            if (lastIdx >= 0 && blocks[lastIdx].type === "status") {
              blocks.splice(lastIdx, 1);
            }
          }
        } else if (chunk.kind === "done") {
          // Mark the last assistant block as done streaming.
          const lastIdx = blocks.length - 1;
          if (lastIdx >= 0 && blocks[lastIdx].type === "assistant") {
            const block = { ...blocks[lastIdx] } as MessageBlock & { type: "assistant" };
            block.streaming = false;
            blocks[lastIdx] = block;
          }
          // Remove any remaining status blocks.
          for (let i = blocks.length - 1; i >= 0; i--) {
            if (blocks[i].type === "status") {
              blocks.splice(i, 1);
            }
          }
        }

        set({ messageBlocks: blocks });
        break;
      }

      case "chat_done": {
        set({ streaming: false, streamingRequestId: null });
        // Refresh sessions to update turn count.
        get().loadSessions();
        break;
      }

      case "sessions_listed": {
        const sessions = (message.sessions as Session[]) || [];
        set({ sessions });
        // Auto-select first session if none selected.
        if (!get().activeSessionId && sessions.length > 0) {
          get().selectSession(sessions[0].session_id);
        }
        break;
      }

      case "session_created": {
        const session = message.session as Session;
        set((s) => ({ sessions: [session, ...s.sessions] }));
        get().selectSession(session.session_id);
        break;
      }

      case "session_deleted": {
        const deletedId = message.session_id as string;
        set((s) => ({
          sessions: s.sessions.filter((sess) => sess.session_id !== deletedId),
          activeSessionId: s.activeSessionId === deletedId ? null : s.activeSessionId,
        }));
        break;
      }

      case "session_renamed": {
        const renamedId = message.session_id as string;
        const newName = message.name as string;
        set((s) => ({
          sessions: s.sessions.map((sess) =>
            sess.session_id === renamedId ? { ...sess, name: newName } : sess
          ),
        }));
        break;
      }

      case "messages_loaded": {
        const messages = (message.messages as ChatMessage[]) || [];
        set({ historicalMessages: messages });
        // Convert historical messages to blocks for display.
        const blocks: MessageBlock[] = [];
        for (const msg of messages) {
          blocks.push({
            id: nextBlockId(),
            type: msg.role,
            content: msg.content,
            streaming: false,
          } as MessageBlock);
        }
        set({ messageBlocks: blocks });
        break;
      }

      case "books_listed": {
        set({ books: (message.books as Book[]) || [] });
        break;
      }

      case "error": {
        console.error("[bookscout]", message.error);
        // If error during streaming, stop streaming.
        if (state.streaming) {
          set({ streaming: false, streamingRequestId: null });
        }
        break;
      }
    }
  },
}));
