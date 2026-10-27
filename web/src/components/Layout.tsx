import { Bot, Code2, Laptop, MessageSquare, Plus, Settings, Sparkles, Target, Trash2, X } from "lucide-react";
import { ReactNode } from "react";
import { api } from "../api";
import { Screen, useStore } from "../store";
import { Avatar } from "./Avatar";
import { Capsule } from "./Capsule";

const NAV: [Screen, string, typeof MessageSquare][] = [
  ["chat", "Chats", MessageSquare],
  ["goals", "Goals", Target],
  ["memory", "Memory", Sparkles],
  ["devices", "Devices", Laptop],
  ["coding", "Coding", Code2],
  ["avatar", "Look", Bot],
  ["settings", "Settings", Settings],
];

function ThreadList() {
  const { threads, running, param, screen, go, refreshThreads, approvals } = useStore();
  const needsYou = new Set(approvals.map((a) => a.conversation_id));
  const main = threads.filter((t) => t.kind === "main");
  const side = threads.filter((t) => t.kind !== "main");
  const row = (t: (typeof threads)[number]) => (
    <a
      key={t.id}
      href={`#/chat/${t.id}`}
      className={`group flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm ${screen === "chat" && param === t.id ? "bg-accent-soft" : "hover:bg-panel"}`}
    >
      <span className="min-w-0 flex-1 truncate">{t.title}</span>
      {needsYou.has(t.id) && <span className="h-2 w-2 rounded-full bg-warn" title="Needs your OK" />}
      {running.has(t.id) && !needsYou.has(t.id) && <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />}
      <button
        title="Delete"
        className="cursor-pointer text-muted opacity-0 group-hover:opacity-100"
        onClick={async (e) => {
          e.preventDefault();
          if (!confirm(`Delete “${t.title}”?`)) return;
          await api(`/api/threads/${t.id}`, { method: "DELETE" });
          if (param === t.id) go("chat");
          refreshThreads();
        }}
      >
        <Trash2 size={13} />
      </button>
    </a>
  );
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
      {main.map(row)}
      {side.length > 0 && <div className="mt-3 px-2.5 pb-1 text-xs font-medium uppercase tracking-wide text-muted">Background</div>}
      {side.map(row)}
    </div>
  );
}

export function Layout({ children, drawer, setDrawer }: { children: ReactNode; drawer: boolean; setDrawer: (v: boolean) => void }) {
  const { state, screen, go, connected, toasts, dismiss, hub, approvals } = useStore();
  const name = state?.config.name ?? "Nemo";
  return (
    <div className="flex h-dvh">
      {/* sidebar: always on desktop, a drawer on phones */}
      <aside
        className={`fixed inset-y-0 left-0 z-30 flex w-72 flex-col gap-3 border-r border-line bg-side p-3 transition-transform md:static md:translate-x-0 ${drawer ? "translate-x-0 shadow-xl" : "-translate-x-full"}`}
      >
        <div className="flex items-center gap-2.5 px-1 pt-1">
          <Avatar size={36} />
          <div className="min-w-0 flex-1">
            <div className="font-bold leading-tight">{name}</div>
            <div className="flex items-center gap-1.5 truncate text-xs text-muted">
              <span className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-ok" : "bg-danger"}`} />
              {state?.config.ready ? state.config.llm.model : "no model yet"}
              {hub?.configured && ` · ${hub.online ? "hub online" : "hub offline"}`}
            </div>
          </div>
          <button className="cursor-pointer text-muted md:hidden" onClick={() => setDrawer(false)}>
            <X size={18} />
          </button>
        </div>
        <button
          onClick={() => {
            go("chat");
            setDrawer(false);
          }}
          className="flex cursor-pointer items-center justify-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-accent-ink hover:brightness-110"
        >
          <Plus size={16} /> New chat
        </button>
        <ThreadList />
        <nav className="hidden flex-col gap-0.5 border-t border-line pt-2 md:flex">
          {NAV.slice(1).map(([s, label, Icon]) => (
            <button
              key={s}
              onClick={() => go(s)}
              className={`flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm ${screen === s ? "bg-panel font-medium" : "hover:bg-panel"}`}
            >
              <Icon size={16} /> {label}
            </button>
          ))}
        </nav>
      </aside>
      {drawer && <div className="fixed inset-0 z-20 bg-black/30 md:hidden" onClick={() => setDrawer(false)} />}

      <main className="flex min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1">{children}</div>
        {/* bottom tabs on phones */}
        <nav className="safe-bottom flex border-t border-line bg-panel md:hidden">
          {NAV.filter(([s]) => s !== "avatar").map(([s, label, Icon]) => (
            <button
              key={s}
              onClick={() => go(s)}
              className={`relative flex flex-1 cursor-pointer flex-col items-center gap-0.5 py-2 text-[11px] ${screen === s ? "text-accent" : "text-muted"}`}
            >
              <Icon size={19} />
              {label}
              {s === "chat" && approvals.length > 0 && <span className="absolute right-[30%] top-1.5 h-2 w-2 rounded-full bg-warn" />}
            </button>
          ))}
        </nav>
      </main>

      <Capsule />
      <div className="fixed bottom-20 right-4 z-50 flex max-w-[min(380px,calc(100vw-32px))] flex-col gap-2 md:bottom-4">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`rounded-xl border border-line border-l-4 bg-panel px-3.5 py-2.5 text-sm shadow-lg ${t.kind === "error" ? "border-l-danger" : t.kind === "warn" ? "border-l-warn" : "border-l-accent"}`}
          >
            <div className="flex gap-2">
              <span className="flex-1">{t.text}</span>
              <button className="cursor-pointer text-muted" onClick={() => dismiss(t.id)}>
                <X size={14} />
              </button>
            </div>
            {t.action && (
              <button
                className="mt-1.5 cursor-pointer rounded-md border border-line px-2 py-0.5 text-[13px] hover:bg-ink/5"
                onClick={() => {
                  t.action!.run();
                  dismiss(t.id);
                }}
              >
                {t.action.label}
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
