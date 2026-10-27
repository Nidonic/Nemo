import { KeyRound, Shield, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { Button, Card, Empty, Field, Input, Page, Pill, Select, Tabs, TextArea } from "../components/ui";
import { useStore } from "../store";

function ModelSettings() {
  const { state, refreshState, toast } = useStore();
  const c = state!.config;
  const [llm, setLlm] = useState({ ...c.llm, api_key: "" });
  const [agent, setAgent] = useState({ name: c.name, language: c.language, workspace: c.workspace, max_steps: c.agent.max_steps });
  const [mcp, setMcp] = useState(Object.keys(c.mcp_servers).length ? JSON.stringify(c.mcp_servers, null, 2) : "");
  const set = (k: string, v: any) => setLlm((l: any) => ({ ...l, [k]: v }));

  const save = async () => {
    let servers = {};
    if (mcp.trim()) {
      try {
        servers = JSON.parse(mcp);
      } catch {
        return toast("MCP servers must be valid JSON.", "error");
      }
    }
    const body: any = {
      name: agent.name || "Nemo",
      language: agent.language || "auto",
      workspace: agent.workspace,
      agent: { ...c.agent, max_steps: +agent.max_steps || 40 },
      llm: { ...llm },
      mcp_servers: servers,
    };
    if (!llm.api_key) delete body.llm.api_key;
    try {
      await api("/api/config", { method: "PUT", body });
      await refreshState();
      setLlm((l: any) => ({ ...l, api_key: "" }));
      toast("Saved.");
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  return (
    <>
      <Card className="flex flex-col gap-3">
        <h3 className="font-semibold">Model</h3>
        <div className="flex flex-wrap gap-3">
          <Field label="Provider">
            <Select
              value={llm.provider}
              onChange={(e) => {
                const p = state!.providers[e.target.value];
                setLlm((l: any) => ({ ...l, provider: e.target.value, kind: p.kind, base_url: p.base_url || l.base_url, model: p.model || l.model }));
              }}
            >
              {Object.keys(state!.providers).map((p) => (
                <option key={p} value={p} disabled={p === "relay" && !state!.hub.configured}>
                  {p === "relay" ? "relay (free allowance)" : p}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Protocol">
            <Select value={llm.kind} onChange={(e) => set("kind", e.target.value)}>
              <option value="openai">OpenAI-compatible</option>
              <option value="anthropic">Anthropic</option>
              <option value="mock">Mock (offline demo)</option>
            </Select>
          </Field>
        </div>
        {llm.provider !== "relay" && (
          <Field label="Base URL">
            <Input value={llm.base_url} onChange={(e) => set("base_url", e.target.value)} />
          </Field>
        )}
        <Field label="Model">
          <Input value={llm.model} onChange={(e) => set("model", e.target.value)} />
        </Field>
        {llm.provider !== "relay" && (
          <Field label="API key" hint="Stored encrypted in the vault, never in config.json.">
            <Input
              type="password"
              autoComplete="off"
              value={llm.api_key}
              onChange={(e) => set("api_key", e.target.value)}
              placeholder={c.llm.api_key_set ? `saved${c.llm.api_key_hint ? " " + c.llm.api_key_hint : ""} — leave empty to keep` : "paste your key"}
            />
          </Field>
        )}
        <details className="text-sm">
          <summary className="cursor-pointer text-muted">Pictures and semantic memory</summary>
          <div className="mt-3 flex flex-col gap-3">
            <Field label="Image model (for the avatar studio)" hint="Same base URL and key; leave empty if your provider has none.">
              <Input value={llm.image_model} onChange={(e) => set("image_model", e.target.value)} placeholder="e.g. gpt-image-1" />
            </Field>
            <div className="flex flex-wrap gap-3">
              <Field label="Embeddings base URL">
                <Input value={llm.embeddings_base_url} onChange={(e) => set("embeddings_base_url", e.target.value)} />
              </Field>
              <Field label="Embeddings model">
                <Input value={llm.embeddings_model} onChange={(e) => set("embeddings_model", e.target.value)} />
              </Field>
            </div>
            <Field label="Embeddings API key">
              <Input type="password" value={llm.embeddings_api_key ?? ""} onChange={(e) => set("embeddings_api_key", e.target.value)} />
            </Field>
          </div>
        </details>
      </Card>

      <Card className="flex flex-col gap-3">
        <h3 className="font-semibold">Agent</h3>
        <div className="flex flex-wrap gap-3">
          <Field label="Name">
            <Input value={agent.name} onChange={(e) => setAgent({ ...agent, name: e.target.value })} />
          </Field>
          <Field label="Reply language">
            <Input value={agent.language} onChange={(e) => setAgent({ ...agent, language: e.target.value })} placeholder="auto" />
          </Field>
          <Field label="Max steps per message">
            <Input type="number" value={agent.max_steps} onChange={(e) => setAgent({ ...agent, max_steps: +e.target.value })} />
          </Field>
        </div>
        <Field label="Workspace folder">
          <Input value={agent.workspace} onChange={(e) => setAgent({ ...agent, workspace: e.target.value })} />
        </Field>
        <Field
          label="MCP servers (JSON; restart Nemo to apply)"
          hint={Object.entries(state!.mcp).map(([k, v]) => `${k}: ${v}`).join(" · ") || 'e.g. {"fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/me"], "env": {"TOKEN": "{{vault:FS_TOKEN}}"}}}'}
        >
          <TextArea rows={4} value={mcp} onChange={(e) => setMcp(e.target.value)} spellCheck={false} className="font-mono !text-[13px]" />
        </Field>
      </Card>
      <div>
        <Button variant="primary" onClick={save}>
          Save settings
        </Button>
      </div>
    </>
  );
}

function SafetySettings() {
  const { state, refreshState, toast } = useStore();
  const s = state!.config.sentinel;
  const [mode, setMode] = useState(s.mode);
  const [allow, setAllow] = useState((s.egress_allowlist as string[]).join("\n"));
  const [deny, setDeny] = useState((s.deny_tools as string[]).join(", "));
  const [grants, setGrants] = useState<{ key: string; scope: string; expires?: number | null }[]>([]);
  const [audit, setAudit] = useState<any[]>([]);
  const [secret, setSecret] = useState({ name: "", value: "" });
  const loadGrants = () => api("/api/grants").then(setGrants);
  useEffect(() => {
    loadGrants();
    api<any[]>("/api/audit?n=40").then((a) => setAudit(a.reverse()));
  }, []);

  const save = () =>
    api("/api/config", {
      method: "PUT",
      body: {
        sentinel: {
          ...s,
          mode,
          egress_allowlist: allow.split(/\s+/).filter(Boolean),
          deny_tools: deny.split(/[,\s]+/).filter(Boolean),
        },
      },
    })
      .then(refreshState)
      .then(() => toast("Saved."))
      .catch((e) => toast(e.message, "error"));

  const MODES: [string, string, string][] = [
    ["ask", "Ask", "Reads and small local changes just happen; anything sensitive asks first."],
    ["strict", "Strict", "Only read-only actions run without asking."],
    ["auto", "Auto", "Nothing asks — except dangerous commands, payments, and sending private data out, which always ask."],
  ];
  return (
    <>
      <Card className="flex flex-col gap-3">
        <h3 className="flex items-center gap-2 font-semibold">
          <Shield size={16} /> Sentinel
        </h3>
        <div className="grid gap-2 sm:grid-cols-3">
          {MODES.map(([v, label, hint]) => (
            <button
              key={v}
              onClick={() => setMode(v)}
              className={`cursor-pointer rounded-xl border p-3 text-left ${mode === v ? "border-accent bg-accent-soft" : "border-line"}`}
            >
              <div className="font-semibold">{label}</div>
              <div className="text-xs text-muted">{hint}</div>
            </button>
          ))}
        </div>
        <Field label="Hosts private data may go to without asking" hint="One per line; subdomains included.">
          <TextArea rows={4} value={allow} onChange={(e) => setAllow(e.target.value)} className="font-mono !text-[13px]" />
        </Field>
        <Field label="Never allow these tools" hint="Comma-separated; globs like mcp__shop__* work.">
          <Input value={deny} onChange={(e) => setDeny(e.target.value)} />
        </Field>
        <div className="flex items-center gap-3 text-sm text-muted">
          <Button variant="primary" onClick={save}>
            Save
          </Button>
          Sandbox: {state!.sandbox ? <Pill tone="ok">bubblewrap active</Pill> : <Pill tone="muted">not available on this OS</Pill>}
        </div>
      </Card>

      <Card className="flex flex-col gap-2">
        <h3 className="font-semibold">Standing approvals</h3>
        {grants.length === 0 && <p className="text-sm text-muted">None. Answers like “always” and “for 24 h” show up here.</p>}
        {grants.map((g) => (
          <div key={g.key + g.scope} className="flex items-center gap-2 text-sm">
            <code className="min-w-0 flex-1 truncate">{g.key}</code>
            <Pill tone="muted">{g.scope}</Pill>
            <button className="cursor-pointer text-muted hover:text-danger" onClick={() => api(`/api/grants/${encodeURIComponent(g.key)}`, { method: "DELETE" }).then(loadGrants)}>
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </Card>

      <Card className="flex flex-col gap-2">
        <h3 className="flex items-center gap-2 font-semibold">
          <KeyRound size={16} /> Vault
        </h3>
        <p className="text-sm text-muted">Encrypted secrets. Refer to them in settings as {"{{vault:NAME}}"}; the model never sees their values.</p>
        <div className="flex flex-wrap gap-1.5">
          {state!.vault.map((n) => (
            <span key={n} className="inline-flex items-center gap-1.5 rounded-full border border-line bg-code py-0.5 pl-2.5 pr-1.5 font-mono text-xs">
              {n}
              <button className="cursor-pointer text-muted hover:text-danger" onClick={() => confirm(`Delete ${n}?`) && api(`/api/vault/${n}`, { method: "DELETE" }).then(refreshState)}>
                <Trash2 size={12} />
              </button>
            </span>
          ))}
        </div>
        <div className="flex gap-2">
          <Input placeholder="NAME" value={secret.name} onChange={(e) => setSecret({ ...secret, name: e.target.value.toUpperCase() })} className="!w-40 font-mono" />
          <Input type="password" placeholder="value" value={secret.value} onChange={(e) => setSecret({ ...secret, value: e.target.value })} />
          <Button
            disabled={!secret.name || !secret.value}
            onClick={() =>
              api(`/api/vault/${secret.name}`, { method: "PUT", body: { value: secret.value } })
                .then(() => setSecret({ name: "", value: "" }))
                .then(refreshState)
                .catch((e) => toast(e.message, "error"))
            }
          >
            Add
          </Button>
        </div>
      </Card>

      <Card className="flex flex-col gap-1.5">
        <h3 className="font-semibold">Recent decisions</h3>
        {audit.length === 0 && <Empty>No calls yet.</Empty>}
        {audit.map((a, i) => (
          <div key={i} className="flex items-center gap-2 text-[13px]">
            <span className="w-16 shrink-0 font-mono text-xs text-muted">{new Date(a.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
            <span className="min-w-0 flex-1 truncate">{a.tool}</span>
            <Pill tone={a.decision === "deny" || a.ok === false ? "danger" : a.via?.startsWith("user") ? "warn" : "muted"}>
              {a.decision ?? a.via ?? ""}
            </Pill>
          </div>
        ))}
      </Card>
    </>
  );
}

export function SettingsScreen() {
  const { state } = useStore();
  const [tab, setTab] = useState<"model" | "safety">("model");
  if (!state) return null;
  return (
    <Page title="Settings" subtitle={`Nemo ${state.version} · ${state.usage.calls} model calls this session`}>
      <Tabs value={tab} onChange={setTab} options={[["model", "Model & agent"], ["safety", "Safety"]]} />
      {tab === "model" ? <ModelSettings /> : <SafetySettings />}
    </Page>
  );
}
