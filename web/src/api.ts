// REST client for the local Nemo daemon.

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const res = await fetch(path, {
    method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
    headers: opts.body !== undefined ? { "Content-Type": "application/json" } : {},
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(data.error || data.detail || res.statusText, res.status);
  return data as T;
}

export type Role = "user" | "assistant" | "tool" | "system";

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, any>;
}

export interface Attachment {
  mime: string;
  width: number;
  height: number;
}

export interface Message {
  id: string;
  role: Role;
  content: string;
  reasoning: string;
  tool_calls: ToolCall[];
  tool_call_id?: string | null;
  name?: string | null;
  ok?: boolean | null;
  attachments: Attachment[];
  created_at: number;
  device: string;
}

export interface ThreadSummary {
  id: string;
  title: string;
  kind: string;
  created: number;
  updated: number;
  routine_id?: string | null;
  goal_id?: string | null;
  origin: string;
  running?: boolean;
}

export interface Thread extends ThreadSummary {
  messages: Message[];
  running: boolean;
  tainted: string[];
}

export interface Approval {
  id: string;
  conversation_id: string;
  tool: string;
  preview: string;
  args: Record<string, any>;
  risk: string;
  reason: string;
  warnings: string[];
  grant_key: string;
  scopes: string[];
  device: string;
}

export interface Device {
  id: string;
  name: string;
  kind: string;
  os: string;
  version: string;
  actions: string[];
  online: boolean;
  last_seen?: number;
}

export interface HubState {
  configured: boolean;
  url: string;
  online: boolean;
  error: string;
  devices: Device[];
  device_id: string;
}

export interface AppState {
  version: string;
  config: any;
  providers: Record<string, { kind: string; base_url: string; model: string; env: string }>;
  categories: string[];
  scopes: string[];
  moods: string[];
  running: string[];
  approvals: Approval[];
  hands: Record<string, { device: string; label: string; active: boolean }>;
  takeovers: string[];
  tools: { name: string; description: string; risk: string }[];
  mcp: Record<string, string>;
  hub: HubState;
  usage: { prompt_tokens: number; completion_tokens: number; calls: number };
  sandbox: boolean;
  vault: string[];
  avatar: { emoji: string; color: string; description?: string; rev: number; faces: Record<string, string> };
}

export const timeAgo = (t: number) => {
  const s = Date.now() / 1000 - t;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString();
};

export const toolLabel = (name: string) => name.replace(/^mcp__([^_]+)__/, "$1 · ").replace(/_/g, " ");
