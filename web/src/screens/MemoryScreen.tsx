import { Moon, Plus, RotateCcw, Search, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { Button, Card, Empty, Input, Page, Pill, Select, Tabs, TextArea } from "../components/ui";
import { useStore } from "../store";

interface Memory {
  id: string;
  content: string;
  category: string;
  source: string;
  created_at: string;
  updated_at: string;
  importance: number;
}

interface Change {
  id: string;
  at: string;
  action: string;
  before: Memory[];
  after: Partial<Memory>;
  reason: string;
  restored: boolean;
}

function Fact({ m, categories, reload }: { m: Memory; categories: string[]; reload: () => void }) {
  const { toast } = useStore();
  const [text, setText] = useState(m.content);
  const [importance, setImportance] = useState(m.importance);
  const save = (body: object) => api(`/api/memory/${m.id}`, { method: "PATCH", body }).then(reload).catch((e) => toast(e.message, "error"));
  return (
    <Card className="flex flex-col gap-2 !p-3">
      <TextArea
        rows={Math.min(4, Math.ceil(text.length / 70) || 1)}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => text.trim() && text !== m.content && save({ content: text })}
        className="resize-none !border-transparent !bg-transparent !px-1 !py-0.5 hover:!border-line"
      />
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <Select value={m.category} onChange={(e) => save({ category: e.target.value })} className="!w-auto !py-0.5 !text-xs">
          {categories.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </Select>
        <label className="flex items-center gap-1.5">
          importance
          <input
            type="range"
            min={0}
            max={1}
            step={0.1}
            value={importance}
            onChange={(e) => setImportance(+e.target.value)}
            onMouseUp={() => save({ importance })}
            onTouchEnd={() => save({ importance })}
            className="w-24 accent-[var(--color-accent)]"
          />
        </label>
        <Pill tone="muted">{m.source}</Pill>
        <span className="flex-1" />
        <button className="cursor-pointer hover:text-danger" title="Forget" onClick={() => api(`/api/memory/${m.id}`, { method: "DELETE" }).then(reload)}>
          <Trash2 size={14} />
        </button>
      </div>
    </Card>
  );
}

function Facts() {
  const { state, toast, on } = useStore();
  const categories = state?.categories ?? ["general"];
  const [items, setItems] = useState<Memory[]>([]);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const [draft, setDraft] = useState("");
  const load = useCallback(
    () => api<Memory[]>(`/api/memory?${new URLSearchParams({ q, ...(cat ? { category: cat } : {}) })}`).then(setItems),
    [q, cat],
  );
  useEffect(() => {
    const t = setTimeout(load, 200);
    return () => clearTimeout(t);
  }, [load]);
  useEffect(() => on("memory_changed", load), [on, load]);
  return (
    <>
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search what Nemo remembers…" className="!pl-9" />
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {["", ...categories].map((c) => (
          <button
            key={c || "all"}
            onClick={() => setCat(c)}
            className={`cursor-pointer rounded-full border px-2.5 py-0.5 text-[13px] ${cat === c ? "border-accent bg-accent-soft text-accent" : "border-line"}`}
          >
            {c || "all"}
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        <Input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Add a fact, e.g. “I'm vegetarian”" />
        <Button
          disabled={!draft.trim()}
          onClick={() =>
            api("/api/memory", { body: { content: draft, category: cat || "general" } })
              .then(() => setDraft(""))
              .then(load)
              .catch((e) => toast(e.message, "error"))
          }
        >
          <Plus size={15} />
        </Button>
      </div>
      {items.length === 0 && <Empty>{q ? "Nothing matches." : "Nothing remembered yet. Nemo saves lasting facts as you talk."}</Empty>}
      {items.map((m) => (
        <Fact key={m.id + m.updated_at + m.category} m={m} categories={categories} reload={load} />
      ))}
    </>
  );
}

function Profile() {
  const { toast } = useStore();
  const [files, setFiles] = useState<{ name: string; content: string }[]>([]);
  const [current, setCurrent] = useState("SOUL.md");
  const [text, setText] = useState("");
  useEffect(() => {
    api<{ name: string; content: string }[]>("/api/profile").then((f) => {
      setFiles(f);
      setText(f.find((x) => x.name === current)?.content ?? "");
    });
  }, [current]);
  return (
    <>
      <div className="flex flex-wrap gap-1.5">
        {files.map((f) => (
          <button
            key={f.name}
            onClick={() => setCurrent(f.name)}
            className={`cursor-pointer rounded-full border px-3 py-1 font-mono text-[13px] ${current === f.name ? "border-accent bg-accent text-accent-ink" : "border-line bg-panel"}`}
          >
            {f.name}
          </button>
        ))}
      </div>
      <TextArea value={text} onChange={(e) => setText(e.target.value)} rows={18} spellCheck={false} className="font-mono !text-[13px]" />
      <div>
        <Button variant="primary" onClick={() => api(`/api/profile/${current}`, { method: "PUT", body: { content: text } }).then(() => toast("Saved."))}>
          Save {current}
        </Button>
      </div>
    </>
  );
}

function HistoryTab() {
  const { toast } = useStore();
  const [changes, setChanges] = useState<Change[]>([]);
  const load = useCallback(() => api<Change[]>("/api/memory/changes").then(setChanges), []);
  useEffect(() => {
    load();
  }, [load]);
  return (
    <>
      <div>
        <Button
          onClick={() =>
            api<unknown[]>("/api/memory/dream", { body: {} }).then((r) => {
              toast(r.length ? `Dream pass: ${r.length} change(s). Each can be undone below.` : "Dream pass: nothing to tidy.");
              load();
            })
          }
        >
          <Moon size={15} /> Tidy up now (dream)
        </Button>
        <p className="mt-1.5 text-xs text-muted">Every night Nemo merges duplicates and replaces outdated facts. Nothing is lost: every change can be undone.</p>
      </div>
      {changes.length === 0 && <Empty>No changes yet.</Empty>}
      {changes.map((c) => (
        <Card key={c.id} className="flex flex-col gap-1.5 !p-3 text-sm">
          <div className="flex items-center gap-2">
            <Pill tone={c.action === "drop" ? "danger" : c.action === "create" ? "ok" : "accent"}>{c.action}</Pill>
            <span className="flex-1 truncate text-xs text-muted">
              {new Date(c.at).toLocaleString()} · {c.reason}
            </span>
            {c.restored ? (
              <Pill tone="muted">undone</Pill>
            ) : (
              <Button small variant="ghost" onClick={() => api(`/api/memory/changes/${c.id}/undo`, { body: {} }).then(load).catch((e) => toast(e.message, "error"))}>
                <RotateCcw size={13} /> Undo
              </Button>
            )}
          </div>
          {c.before.map((b) => (
            <div key={b.id} className="text-muted line-through">
              {b.content}
            </div>
          ))}
          {c.after.content && <div>{c.after.content}</div>}
        </Card>
      ))}
    </>
  );
}

export function MemoryScreen() {
  const [tab, setTab] = useState<"facts" | "profile" | "history">("facts");
  return (
    <Page title="Memory" subtitle="What Nemo knows about you. All of it is yours to read, edit and undo.">
      <Tabs value={tab} onChange={setTab} options={[["facts", "Facts"], ["profile", "Profile files"], ["history", "History"]]} />
      {tab === "facts" ? <Facts /> : tab === "profile" ? <Profile /> : <HistoryTab />}
    </Page>
  );
}
