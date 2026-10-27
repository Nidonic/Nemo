import { Hand, Square } from "lucide-react";
import { api } from "../api";
import { useStore } from "../store";
import { Avatar } from "./Avatar";

// The floating capsule shown while Nemo's hands are on a screen: what it is doing, and a big STOP.
export function Capsule() {
  const { hands, go, toast, threads } = useStore();
  const active = Object.entries(hands).filter(([, h]) => h.active);
  if (!active.length) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 top-3 z-40 flex flex-col items-center gap-2 px-4">
      {active.map(([tid, h]) => (
        <div key={tid} className="pointer-events-auto flex max-w-md items-center gap-3 rounded-full border border-line bg-panel/95 py-1.5 pl-1.5 pr-1.5 shadow-lg backdrop-blur">
          <Avatar mood="thinking" size={30} />
          <button className="min-w-0 flex-1 cursor-pointer text-left" onClick={() => go("chat", tid)}>
            <div className="flex items-center gap-1 text-xs text-muted">
              <Hand size={12} /> {h.device === "phone" ? "On your phone" : "On this computer"} · {threads.find((t) => t.id === tid)?.title ?? ""}
            </div>
            <div className="truncate text-sm font-medium">{h.label || "Working…"}</div>
          </button>
          <button
            onClick={() => api(`/api/threads/${tid}/stop`, { body: {} }).catch((e) => toast(e.message, "error"))}
            className="flex cursor-pointer items-center gap-1 rounded-full bg-danger px-3.5 py-2 text-sm font-bold text-white hover:brightness-110"
          >
            <Square size={13} fill="currentColor" /> STOP
          </button>
        </div>
      ))}
    </div>
  );
}
