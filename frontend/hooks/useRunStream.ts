"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type ConnectionState =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "closed"
  | "error";

export type StreamEvent = {
  run_id: string;
  event_type:
    | "node_started"
    | "node_completed"
    | "awaiting_hitl"
    | "resuming"
    | "complete"
    | "failed"
    | "cost_update"
    | "heartbeat";
  node_name: string | null;
  timestamp: string;
  data: Record<string, unknown>;
};

type UseRunStreamReturn = {
  latestEvent: StreamEvent | null;
  connectionState: ConnectionState;
  error: string | null;
};

const MAX_RECONNECT_ATTEMPTS = 10;
const INITIAL_RECONNECT_DELAY_MS = 1000;
const MAX_RECONNECT_DELAY_MS = 30000;

const TERMINAL_EVENTS: ReadonlySet<StreamEvent["event_type"]> = new Set([
  "complete",
  "failed",
]);

export function useRunStream(
  tenderId: string | null,
): UseRunStreamReturn {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const receivedTerminalRef = useRef(false);

  const [latestEvent, setLatestEvent] = useState<StreamEvent | null>(null);
  const [connectionState, setConnectionState] =
    useState<ConnectionState>("closed");
  const [error, setError] = useState<string | null>(null);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!tenderId) {
      receivedTerminalRef.current = false;
      reconnectAttemptRef.current = 0;
      clearReconnectTimer();

      const ws = wsRef.current;
      if (ws) {
        ws.onclose = null;
        ws.onerror = null;
        ws.onmessage = null;
        ws.close(1000, "tenderId cleared");
        wsRef.current = null;
      }

      setLatestEvent(null);
      setConnectionState("closed");
      setError(null);
      return;
    }

    receivedTerminalRef.current = false;
    reconnectAttemptRef.current = 0;

    const doConnect = () => {
      if (receivedTerminalRef.current) return;

      clearReconnectTimer();

      const prev = wsRef.current;
      if (prev) {
        prev.onclose = null;
        prev.onerror = null;
        prev.onmessage = null;
        prev.close(1000, "reconnecting");
        wsRef.current = null;
      }

      const wsBaseUrl = process.env.NEXT_PUBLIC_WS_BASE_URL;
      if (!wsBaseUrl) {
        setError("NEXT_PUBLIC_WS_BASE_URL is not configured");
        setConnectionState("error");
        return;
      }

      const apiKey = process.env.NEXT_PUBLIC_API_KEY;
      if (!apiKey) {
        setError("NEXT_PUBLIC_API_KEY is not configured");
        setConnectionState("error");
        return;
      }

      setConnectionState("connecting");
      setError(null);

      const url = `${wsBaseUrl}/tenders/${encodeURIComponent(tenderId)}/stream?token=${encodeURIComponent(apiKey)}`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttemptRef.current = 0;
        setConnectionState("connected");
        setError(null);
      };

      ws.onmessage = (event: MessageEvent) => {
        try {
          const parsed: StreamEvent = JSON.parse(
            event.data as string,
          ) as StreamEvent;
          setLatestEvent(parsed);

          if (TERMINAL_EVENTS.has(parsed.event_type)) {
            receivedTerminalRef.current = true;
            setConnectionState("closed");
            clearReconnectTimer();
            ws.close(1000, "terminal event");
            wsRef.current = null;
          }
        } catch {
          /* ignore malformed JSON frames */
        }
      };

      ws.onclose = (event: CloseEvent) => {
        if (event.code === 4003) {
          setConnectionState("error");
          setError("Unauthorised — WebSocket access denied.");
          receivedTerminalRef.current = true;
          wsRef.current = null;
          return;
        }

        if (receivedTerminalRef.current) {
          setConnectionState("closed");
          wsRef.current = null;
          return;
        }

        if (reconnectAttemptRef.current >= MAX_RECONNECT_ATTEMPTS) {
          setConnectionState("error");
          setError("Maximum reconnection attempts reached.");
          wsRef.current = null;
          return;
        }

        setConnectionState("reconnecting");

        const delay = Math.min(
          INITIAL_RECONNECT_DELAY_MS *
            Math.pow(2, reconnectAttemptRef.current),
          MAX_RECONNECT_DELAY_MS,
        );

        reconnectAttemptRef.current += 1;

        reconnectTimerRef.current = setTimeout(() => {
          doConnect();
        }, delay);
      };

      ws.onerror = () => {
        /* onclose will fire after onerror — reconnect logic lives there */
      };
    };

    doConnect();

    return () => {
      receivedTerminalRef.current = true;
      clearReconnectTimer();

      const ws = wsRef.current;
      if (ws) {
        ws.onclose = null;
        ws.onerror = null;
        ws.onmessage = null;
        ws.close(1000, "unmount");
        wsRef.current = null;
      }
    };
  }, [tenderId, clearReconnectTimer]);

  return { latestEvent, connectionState, error };
}
