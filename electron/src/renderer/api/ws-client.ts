/**
 * WebSocket client for the BookScout REPL backend.
 *
 * Connects to the Python FastAPI WebSocket server and provides:
 * - Automatic reconnection with backoff
 * - Request/response correlation via request_id
 * - Typed message handling
 */

import type {
  RequestType,
  StreamChunkMessage,
  WsRequest,
  WsResponse,
} from "@shared/types";

let nextRequestId = 1;

/** Generate a unique request ID. */
export function requestId(): string {
  return `req_${nextRequestId++}`;
}

type MessageHandler = (message: WsResponse) => void;

export class WsClient {
  private ws: WebSocket | null = null;
  private url: string;
  private handlers: Set<MessageHandler> = new Set();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelay = 500;
  private maxReconnectDelay = 5000;
  private stopped = false;

  constructor(port: number) {
    this.url = `ws://127.0.0.1:${port}/ws`;
  }

  /** Connect to the WebSocket server. */
  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.stopped = false;
      const ws = new WebSocket(this.url);

      ws.onopen = () => {
        this.reconnectDelay = 500;
        resolve();
      };

      ws.onmessage = (event) => {
        try {
          const message: WsResponse = JSON.parse(event.data);
          this.handlers.forEach((handler) => handler(message));
        } catch {
          // Ignore malformed messages.
        }
      };

      ws.onclose = () => {
        this.ws = null;
        if (!this.stopped) {
          this.scheduleReconnect();
        }
      };

      ws.onerror = (err) => {
        if (!this.ws) {
          reject(err);
        }
      };

      this.ws = ws;
    });
  }

  /** Disconnect and stop reconnecting. */
  disconnect() {
    this.stopped = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  /** Send a request to the server. Returns the request_id for correlation. */
  send(type: RequestType, payload: Omit<WsRequest, "type" | "request_id"> = {}): string {
    const id = requestId();
    const request: WsRequest = { type, request_id: id, ...payload };
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(request));
    }
    return id;
  }

  /** Register a message handler. Returns an unsubscribe function. */
  onMessage(handler: MessageHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  /** Whether the WebSocket is currently connected. */
  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  private scheduleReconnect() {
    this.reconnectTimer = setTimeout(async () => {
      try {
        await this.connect();
      } catch {
        // Exponential backoff on failure.
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay);
        this.scheduleReconnect();
      }
    }, this.reconnectDelay);
  }
}

/** Singleton client instance — initialized once in App.tsx. */
let client: WsClient | null = null;

/** Get or create the singleton WsClient. */
export function getWsClient(port: number): WsClient {
  if (!client) {
    client = new WsClient(port);
  }
  return client;
}
