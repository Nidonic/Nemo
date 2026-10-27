import { CalendarClock, CheckCircle2, Circle, Pause, Play, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, timeAgo } from "../api";
import { Button, Card, Empty, Field, Input, Page, Pill, Tabs, TextArea } from "../components/ui";
import { useStore } from "../store";

interface Goal {
  id: string;
  title: string;
  description: string;
  status: "active" | "paused" | "done" | "failed";
  steps: { id: string; text: string; done: boolean; note: string }[];
  check: string;
  next_check: string | null;
  conversation_id: string | null;
  log: { at: string; text: string }[];
  progress: number;
  updated: number;
}

interface Routine {
  id: string;
  name: string;
  schedule: string;
  prompt: string;
  enabled: boolean;
  next_run: string | null;
  last_run: string | null;
  last_status: string | null;
  chat_id: string | null;
  running: boolean;
}

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—");
const SCHEDULES = "every 30m · every 2h · daily 08:00 · weekdays 09:30 · weekly mon 18:00 · once 2026-11-02 14:00";

function GoalCard({ g, reload }: { g: Goal; reload: () => void }) {
  const { go, toast } = useStore();
  const patch = (body: object) => api(`/api/goals/${g.id}`, { method: "PATCH", body }).then(reload).catch((e) => toast(e.message, "error"));
  const tone = { active: "accent", paused: "muted", done: "ok", failed: "danger" }[g.status] as "accent";
  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="min-w-0 flex-1 font-semibold">{g.title}</h3>
        <Pill tone={tone}>{g.status}</Pill>
      </div>
      {g.description && <p className="text-sm text-muted">{g.description}</p>}
      <div className="h-2 overflow-hidden rounded-full bg-code">
        <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${Math.round(g.progress * 100)}%` }} />
      </div>
      <ul className="flex flex-col gap-1">
        {g.steps.map((s) => (
          <li key={s.id} className="flex items-start gap-2 text-sm">
            <button disabled={s.done} className="mt-0.5 cursor-pointer text-accent disabled:cursor-default" onClick={() => patch({ done_steps: [s.id] })}>
              {s.done ? <CheckCircle2 size={16} /> : <Circle size={16} className="text-muted" />}
            </button>
            <span className={s.done ? "text-muted line-through" : ""}>
              {s.text}
              {s.note && <span className="ml-1 text-muted">— {s.note}</span>}
            </span>
          </li>
        ))}
      </ul>
      {g.log.length > 0 && (
        <div className="border-l-2 border-line pl-3 text-[13px] text-muted">
          {g.log.slice(-3).map((l, i) => (
            <div key={i}>
              <span className="font-mono">{l.at.slice(5, 16).replace("T", " ")}</span> {l.text}
            </div>
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-1.5 text-[13px] text-muted">
        <CalendarClock size={14} />
        {g.check ? `checks in ${g.check}; next ${when(g.next_check)}` : "checks in only when asked"} · updated {timeAgo(g.updated)}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {g.status === "active" && (
          <Button small onClick={() => api(`/api/goals/${g.id}/check`, { body: {} }).then(() => toast("Checking in now."))}>
            <RefreshCw size={13} /> Check in now
          </Button>
        )}
        {g.status === "active" ? (
          <Button small onClick={() => patch({ status: "paused" })}>
            <Pause size={13} /> Pause
          </Button>
        ) : g.status === "paused" ? (
          <Button small onClick={() => patch({ status: "active" })}>
            <Play size={13} /> Resume
          </Button>
        ) : null}
        {g.conversation_id && (
          <Button small variant="ghost" onClick={() => go("chat", g.conversation_id!)}>
            Open chat
          </Button>
        )}
        <Button small variant="danger" onClick={() => confirm(`Delete “${g.title}”?`) && api(`/api/goals/${g.id}`, { method: "DELETE" }).then(reload)}>
          <Trash2 size={13} />
        </Button>
      </div>
    </Card>
  );
}

function NewGoal({ reload }: { reload: () => void }) {
  const { toast } = useStore();
  const [title, setTitle] = useState("");
  const [steps, setSteps] = useState("");
  const [check, setCheck] = useState("daily 09:00");
  return (
    <Card className="flex flex-col gap-3">
      <Field label="Goal">
        <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Find a flat in Hangzhou under ¥5000" />
      </Field>
      <Field label="Steps, one per line (Nemo can add more)">
        <TextArea rows={3} value={steps} onChange={(e) => setSteps(e.target.value)} placeholder={"Check listing sites\nShortlist five\nBook viewings"} />
      </Field>
      <Field label="Check in on its own" hint={SCHEDULES}>
        <Input value={check} onChange={(e) => setCheck(e.target.value)} placeholder="leave empty for never" />
      </Field>
      <div>
        <Button
          variant="primary"
          disabled={!title.trim()}
          onClick={async () => {
            try {
              await api("/api/goals", { body: { title, steps: steps.split("\n").filter(Boolean), check } });
              setTitle("");
              setSteps("");
              reload();
            } catch (e: any) {
              toast(e.message, "error");
            }
          }}
        >
          Start goal
        </Button>
      </div>
    </Card>
  );
}

function Routines() {
  const { toast, go, on } = useStore();
  const [items, setItems] = useState<Routine[]>([]);
  const [form, setForm] = useState({ name: "", schedule: "", prompt: "" });
  const load = useCallback(() => api<Routine[]>("/api/routines").then(setItems), []);
  useEffect(() => {
    load();
    return on("routines_changed", load);
  }, [load, on]);
  const act = (p: Promise<unknown>) => p.then(load).catch((e) => toast(e.message, "error"));
  return (
    <>
      <Card className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-3">
          <Field label="Name">
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Morning brief" />
          </Field>
          <Field label="Schedule">
            <Input value={form.schedule} onChange={(e) => setForm({ ...form, schedule: e.target.value })} placeholder="daily 08:00" />
          </Field>
        </div>
        <Field label="What to do" hint={SCHEDULES}>
          <TextArea rows={2} value={form.prompt} onChange={(e) => setForm({ ...form, prompt: e.target.value })} placeholder="Check the weather and top news, then write me a short brief." />
        </Field>
        <div>
          <Button
            variant="primary"
            disabled={!form.name || !form.schedule || !form.prompt}
            onClick={() => act(api("/api/routines", { body: form }).then(() => setForm({ name: "", schedule: "", prompt: "" })))}
          >
            Add routine
          </Button>
        </div>
      </Card>
      {items.length === 0 && <Empty>No routines. Or just tell Nemo: “every morning at 8, brief me on the news”.</Empty>}
      {items.map((r) => (
        <Card key={r.id} className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <h3 className="flex-1 font-semibold">{r.name}</h3>
            <Pill tone={r.enabled ? "accent" : "muted"}>{r.running ? "running" : r.enabled ? "on" : "off"}</Pill>
          </div>
          <div className="text-[13px] text-muted">
            <code>{r.schedule}</code> · next {when(r.next_run)} · last {when(r.last_run)} {r.last_status && `(${r.last_status})`}
          </div>
          <p className="whitespace-pre-wrap text-sm">{r.prompt}</p>
          <div className="flex flex-wrap gap-1.5">
            <Button small onClick={() => act(api(`/api/routines/${r.id}`, { method: "PATCH", body: { enabled: !r.enabled } }))}>
              {r.enabled ? "Pause" : "Resume"}
            </Button>
            <Button small onClick={() => act(api(`/api/routines/${r.id}/run`, { body: {} }).then(() => toast("Started.")))}>
              Run now
            </Button>
            {r.chat_id && (
              <Button small variant="ghost" onClick={() => go("chat", r.chat_id!)}>
                Open chat
              </Button>
            )}
            <Button small variant="danger" onClick={() => confirm(`Delete “${r.name}”?`) && act(api(`/api/routines/${r.id}`, { method: "DELETE" }))}>
              <Trash2 size={13} />
            </Button>
          </div>
        </Card>
      ))}
    </>
  );
}

export function GoalsScreen() {
  const { on } = useStore();
  const [tab, setTab] = useState<"goals" | "routines">("goals");
  const [goals, setGoals] = useState<Goal[]>([]);
  const load = useCallback(() => api<Goal[]>("/api/goals").then(setGoals), []);
  useEffect(() => {
    load();
    return on("goals_changed", load);
  }, [load, on]);
  const active = goals.filter((g) => g.status === "active" || g.status === "paused");
  const finished = goals.filter((g) => g.status === "done" || g.status === "failed");
  return (
    <Page title="Goals" subtitle="Long-running work Nemo keeps moving on its own, and routines it runs on a schedule.">
      <Tabs value={tab} onChange={setTab} options={[["goals", "Goals"], ["routines", "Routines"]]} />
      {tab === "routines" ? (
        <Routines />
      ) : (
        <>
          <NewGoal reload={load} />
          {active.length === 0 && <Empty>No active goals. Start one above, or ask Nemo to take something on.</Empty>}
          {active.map((g) => (
            <GoalCard key={g.id} g={g} reload={load} />
          ))}
          {finished.length > 0 && <h2 className="mt-2 text-sm font-semibold text-muted">Finished</h2>}
          {finished.map((g) => (
            <GoalCard key={g.id} g={g} reload={load} />
          ))}
        </>
      )}
    </Page>
  );
}
