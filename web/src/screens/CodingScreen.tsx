import { Play, Square, Terminal } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, timeAgo } from "../api";
import { Button, Card, Empty, Field, Input, Page, Pill, Select, TextArea } from "../components/ui";
import { useStore } from "../store";

interface Agent {
  id: string;
  label: string;
  installed: boolean;
  has_sessions: boolean;
}
interface Session {
  agent: string;
  id: string;
  path: string;
  project: string;
  updated: number;
  title: string;
}
interface Run {
  id: string;
  agent: string;
  prompt: string;
  cwd: string;
  started: number;
  status: string;
  lines: { role: string; text: string }[];
}

function Transcript({ lines }: { lines: { role: string; text: string }[] }) {
  return (
    <div className="flex max-h-[420px] flex-col gap-2 overflow-y-auto rounded-lg bg-code p-3 text-[13px]">
      {lines.map((l, i) => (
        <div key={i} className={l.role === "user" ? "font-medium" : l.role === "log" ? "font-mono text-xs text-muted" : ""}>
          <span className="mr-1.5 text-xs uppercase text-muted">{l.role}</span>
          <span className="whitespace-pre-wrap break-words">{l.text.length > 2000 ? l.text.slice(0, 2000) + " …" : l.text}</span>
        </div>
      ))}
      {lines.length === 0 && <span className="text-muted">Nothing yet.</span>}
    </div>
  );
}

export function CodingScreen() {
  const { toast, on, state } = useStore();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [open, setOpen] = useState<{ title: string; lines: { role: string; text: string }[] } | null>(null);
  const [form, setForm] = useState({ agent: "claude", cwd: "", prompt: "" });

  const load = useCallback(
    () =>
      api<{ agents: Agent[]; sessions: Session[]; runs: Run[] }>("/api/coding").then((d) => {
        setAgents(d.agents);
        setSessions(d.sessions);
        setRuns(d.runs.reverse());
      }),
    [],
  );
  useEffect(() => {
    load();
    const off1 = on("coding", (ev) => setRuns((r) => [ev.run, ...r.filter((x) => x.id !== ev.run.id)]));
    const off2 = on("coding_line", (ev) => setRuns((r) => r.map((x) => (x.id === ev.run_id ? { ...x, lines: [...x.lines, ev.line] } : x))));
    return () => {
      off1();
      off2();
    };
  }, [load, on]);

  const installed = agents.filter((a) => a.installed);
  return (
    <Page title="Coding" subtitle="Claude Code, Codex and Cursor sessions on this computer: read them, or start a new run and watch it.">
      <div className="flex flex-wrap gap-1.5">
        {agents.map((a) => (
          <Pill key={a.id} tone={a.installed ? "ok" : "muted"}>
            {a.label} {a.installed ? "installed" : "not found"}
          </Pill>
        ))}
      </div>

      <Card className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-3">
          <Field label="Agent" className="!flex-none">
            <Select value={form.agent} onChange={(e) => setForm({ ...form, agent: e.target.value })}>
              {agents.filter((a) => a.id !== "dsh").map((a) => (
                <option key={a.id} value={a.id} disabled={!a.installed}>
                  {a.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Project folder">
            <Input value={form.cwd} onChange={(e) => setForm({ ...form, cwd: e.target.value })} placeholder={state?.config.workspace} />
          </Field>
        </div>
        <Field label="Task">
          <TextArea rows={3} value={form.prompt} onChange={(e) => setForm({ ...form, prompt: e.target.value })} placeholder="Add a --dry-run flag to the deploy script and test it." />
        </Field>
        <div>
          <Button
            variant="primary"
            disabled={!form.prompt.trim() || !installed.length}
            onClick={() =>
              api("/api/coding/runs", { body: form })
                .then(() => setForm({ ...form, prompt: "" }))
                .catch((e) => toast(e.message, "error"))
            }
          >
            <Play size={14} /> Start
          </Button>
          {!installed.length && <span className="ml-3 text-sm text-muted">Install one of the agents to run tasks here.</span>}
        </div>
      </Card>

      {runs.map((r) => (
        <Card key={r.id} className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <Terminal size={15} />
            <span className="min-w-0 flex-1 truncate font-medium">{r.prompt}</span>
            <Pill tone={r.status === "running" ? "accent" : r.status === "done" ? "ok" : "muted"}>{r.status}</Pill>
            {r.status === "running" && (
              <Button small variant="danger" onClick={() => api(`/api/coding/runs/${r.id}/stop`, { body: {} })}>
                <Square size={12} />
              </Button>
            )}
          </div>
          <div className="text-xs text-muted">
            {r.agent} · {r.cwd}
          </div>
          <Transcript lines={r.lines} />
        </Card>
      ))}

      <h2 className="mt-2 text-sm font-semibold text-muted">Recent sessions</h2>
      {sessions.length === 0 && <Empty>No coding-agent sessions found in ~/.claude, ~/.codex or ~/.cursor.</Empty>}
      {sessions.map((s) => (
        <button
          key={s.path}
          className="flex cursor-pointer items-center gap-3 rounded-xl border border-line bg-panel px-3.5 py-2.5 text-left hover:border-accent"
          onClick={() =>
            api<{ role: string; text: string }[]>(`/api/coding/transcript?path=${encodeURIComponent(s.path)}`)
              .then((lines) => setOpen({ title: s.title, lines }))
              .catch((e) => toast(e.message, "error"))
          }
        >
          <Pill tone="muted">{s.agent}</Pill>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium">{s.title}</span>
            <span className="block truncate text-xs text-muted">{s.project}</span>
          </span>
          <span className="shrink-0 text-xs text-muted">{timeAgo(s.updated)}</span>
        </button>
      ))}

      {open && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 md:items-center md:p-6" onClick={() => setOpen(null)}>
          <div className="flex max-h-[85vh] w-full max-w-2xl flex-col gap-3 rounded-t-2xl bg-panel p-4 md:rounded-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center">
              <h3 className="flex-1 truncate font-semibold">{open.title}</h3>
              <Button small variant="ghost" onClick={() => setOpen(null)}>
                Close
              </Button>
            </div>
            <Transcript lines={open.lines} />
          </div>
        </div>
      )}
    </Page>
  );
}
