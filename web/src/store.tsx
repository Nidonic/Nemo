// Global state: the daemon's state, conversations, live turns, approvals and the SSE event stream.
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, AppState, Approval, HubState, ThreadSummary } from "./api";

export type Block =
  | { type: "text"; text: string; done: boolean }
  | { type: "reasoning"; text: string }
  | { type: "tool"; id: string; name: string; summary: string; args: any; status: "run" | "ok" | "err" | "refused"; output?: string; images?: number }
  | { type: "remote"; device: string; stage: string; text: string };

export interface Toast {
  id: number;
  text: string;
  kind: "info" | "warn" | "error";
  action?: { label: string; run: () => void };
}

export type Screen = "chat" | "goals" | "memory" | "devices" | "coding" | "avatar" | "settings";

type Listener = (event: any) => void;

interface Store {
  state: AppState | null;
  refreshState: () => Promise<void>;
  threads: ThreadSummary[];
  refreshThreads: () => Promise<void>;
  live: Record<string, Block[]>;
  running: Set<string>;
  approvals: Approval[];
  hands: Record<string, { device: string; label: string; active: boolean }>;
  takeovers: Record<string, string>;
  hub: HubState | null;
  versions: Record<string, number>;
  connected: boolean;
  toasts: Toast[];
  toast: (text: string, kind?: Toast["kind"], action?: Toast["action"]) => void;
  dismiss: (id: number) => void;
  screen: Screen;
  param: string;
  go: (screen: Screen, param?: string) => void;
  on: (type: string, fn: Listener) => () => void;
}

const Ctx = createContext<Store | null>(null);

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore outside StoreProvider");
  return s;
}

function parseHash(): [Screen, string] {
  const m = location.hash.match(/^#\/(\w+)\/?(.*)$/);
  const screens: Screen[] = ["chat", "goals", "memory", "devices", "coding", "avatar", "settings"];
  if (m && screens.includes(m[1] as Screen)) return [m[1] as Screen, decodeURIComponent(m[2] || "")];
  return ["chat", ""];
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AppState | null>(null);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [live, setLive] = useState<Record<string, Block[]>>({});
  const [running, setRunning] = useState<Set<string>>(new Set());
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [hands, setHands] = useState<Store["hands"]>({});
  const [takeovers, setTakeovers] = useState<Record<string, string>>({});
  const [hub, setHub] = useState<HubState | null>(null);
  const [versions, setVersions] = useState<Record<string, number>>({});
  const [connected, setConnected] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [[screen, param], setRoute] = useState<[Screen, string]>(parseHash());
  const listeners = useRef(new Map<string, Set<Listener>>());
  const threadsRef = useRef(threads);
  threadsRef.current = threads;

  const toast = useCallback((text: string, kind: Toast["kind"] = "info", action?: Toast["action"]) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind, action }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), action ? 15000 : 5000);
  }, []);
  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);

  const refreshState = useCallback(async () => {
    const s = await api<AppState>("/api/state");
    setState(s);
    setHub(s.hub);
    document.title = s.config.name;
  }, []);
  const refreshThreads = useCallback(async () => setThreads(await api<ThreadSummary[]>("/api/threads")), []);

  const go = useCallback((s: Screen, p = "") => {
    location.hash = `#/${s}${p ? "/" + encodeURIComponent(p) : ""}`;
  }, []);

  const on = useCallback((type: string, fn: Listener) => {
    if (!listeners.current.has(type)) listeners.current.set(type, new Set());
    listeners.current.get(type)!.add(fn);
    return () => listeners.current.get(type)!.delete(fn);
  }, []);

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    refreshState().catch((e) => toast(`Could not reach Nemo: ${e.message}`, "error"));
    refreshThreads().catch(() => undefined);
  }, [refreshState, refreshThreads, toast]);

  // the live event stream
  useEffect(() => {
    const push = (tid: string, fn: (blocks: Block[]) => Block[]) =>
      setLive((l) => ({ ...l, [tid]: fn(l[tid] ?? []) }));

    const handle = (ev: any) => {
      const tid: string = ev.thread;
      switch (ev.type) {
        case "hello":
          setRunning(new Set(ev.running));
          setApprovals(ev.approvals);
          setHands(ev.hands);
          setTakeovers(Object.fromEntries((ev.takeovers as string[]).map((t) => [t, "Waiting for you"])));
          setHub(ev.hub);
          break;
        case "turn_start":
          setRunning((r) => new Set(r).add(tid));
          setLive((l) => ({ ...l, [tid]: [] }));
          if (!threadsRef.current.some((t) => t.id === tid)) refreshThreads();
          break;
        case "text":
          push(tid, (b) => {
            const last = b[b.length - 1];
            if (last?.type === "text" && !last.done) return [...b.slice(0, -1), { ...last, text: last.text + ev.delta }];
            return [...b, { type: "text", text: ev.delta, done: false }];
          });
          break;
        case "reasoning":
          push(tid, (b) => {
            const last = b[b.length - 1];
            if (last?.type === "reasoning") return [...b.slice(0, -1), { ...last, text: last.text + ev.delta }];
            return [...b, { type: "reasoning", text: ev.delta }];
          });
          break;
        case "step_end":
          push(tid, (b) => b.map((x) => (x.type === "text" ? { ...x, done: true } : x)));
          break;
        case "tool_start":
          push(tid, (b) => [...b, { type: "tool", id: ev.id, name: ev.name, summary: ev.summary, args: ev.args, status: "run" }]);
          break;
        case "tool_end":
          push(tid, (b) =>
            b.map((x) =>
              x.type === "tool" && x.id === ev.id
                ? { ...x, status: ev.refused ? "refused" : ev.ok ? "ok" : "err", output: ev.output, images: ev.images }
                : x,
            ),
          );
          break;
        case "remote":
          push(tid, (b) => {
            const text = ev.stage === "text" ? ev.delta : ev.stage === "tool" ? `⚙ ${ev.name} ${ev.summary ?? ""}` : ev.stage === "tool_result" ? (ev.ok ? "✓" : "✗") + " " + (ev.summary ?? "") : ev.text ?? "";
            const last = b[b.length - 1];
            if (ev.stage === "text" && last?.type === "remote" && last.stage === "text")
              return [...b.slice(0, -1), { ...last, text: last.text + text }];
            return [...b, { type: "remote", device: ev.device, stage: ev.stage, text }];
          });
          break;
        case "approval":
          setApprovals((a) => [...a.filter((x) => x.id !== ev.approval.id), ev.approval]);
          if (location.hash !== `#/chat/${tid}`) {
            const title = threadsRef.current.find((t) => t.id === tid)?.title;
            toast(`Nemo needs your OK${title ? ` in “${title}”` : ""}: ${ev.approval.reason}`, "warn", {
              label: "Open",
              run: () => go("chat", tid),
            });
          }
          break;
        case "approval_done":
          setApprovals((a) => a.filter((x) => x.id !== ev.id));
          break;
        case "hands":
          setHands((h) => {
            const next = { ...h };
            if (ev.active) next[tid] = { device: ev.device, label: ev.label, active: true };
            else delete next[tid];
            return next;
          });
          break;
        case "takeover":
          setTakeovers((t) => ({ ...t, [tid]: ev.reason }));
          toast(`Your turn: ${ev.reason}`, "warn", { label: "Open", run: () => go("chat", tid) });
          break;
        case "takeover_done":
          setTakeovers((t) => {
            const next = { ...t };
            delete next[tid];
            return next;
          });
          break;
        case "turn_end":
          setRunning((r) => {
            const next = new Set(r);
            next.delete(tid);
            return next;
          });
          setVersions((v) => ({ ...v, [tid]: (v[tid] ?? 0) + 1 }));
          refreshThreads();
          if (ev.status?.startsWith("error") && location.hash !== `#/chat/${tid}`) toast(ev.status, "error");
          break;
        case "threads_changed":
          refreshThreads();
          setVersions((v) => {
            const next = { ...v };
            for (const t of ev.threads as string[]) next[t] = (next[t] ?? 0) + 1;
            return next;
          });
          break;
        case "devices":
          setHub((h) => (h ? { ...h, devices: ev.devices, online: true } : h));
          break;
        case "notify":
          toast(`${ev.title}: ${ev.body}`);
          break;
      }
      listeners.current.get(ev.type)?.forEach((fn) => fn(ev));
    };

    const source = new EventSource("/api/events");
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (m) => handle(JSON.parse(m.data));
    return () => source.close();
  }, [go, refreshThreads, toast]);

  const value = useMemo<Store>(
    () => ({
      state, refreshState, threads, refreshThreads, live, running, approvals, hands, takeovers, hub, versions,
      connected, toasts, toast, dismiss, screen, param, go, on,
    }),
    [state, refreshState, threads, refreshThreads, live, running, approvals, hands, takeovers, hub, versions,
      connected, toasts, toast, dismiss, screen, param, go, on],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
