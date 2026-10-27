import { ShieldAlert } from "lucide-react";
import { useState } from "react";
import { api, Approval, toolLabel } from "../api";
import { useStore } from "../store";
import { Button, Pill } from "./ui";

const SCOPE_LABEL: Record<string, string> = {
  once: "Allow once",
  conversation: "In this chat",
  session: "Until restart",
  "24h": "For 24 h",
  always: "Always",
};

export function ApprovalCard({ approval }: { approval: Approval }) {
  const { toast } = useStore();
  const [busy, setBusy] = useState(false);
  const decide = async (allow: boolean, scope = "once") => {
    setBusy(true);
    try {
      await api(`/api/approvals/${approval.id}`, { body: { allow, scope } });
    } catch (e: any) {
      toast(e.message, "error");
      setBusy(false);
    }
  };
  const detail = approval.preview || JSON.stringify(approval.args, null, 2);
  return (
    <div className="flex flex-col gap-2.5 rounded-xl border border-warn/60 bg-warn-soft p-3.5">
      <div className="flex items-start gap-2 font-semibold text-warn">
        <ShieldAlert size={18} className="mt-0.5 shrink-0" />
        <span className="min-w-0 [overflow-wrap:anywhere]">{approval.reason}</span>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <b>{toolLabel(approval.tool)}</b>
        <Pill tone={approval.risk === "sensitive" ? "danger" : "warn"}>{approval.risk}</Pill>
        {approval.device && approval.device !== "local" && !approval.device.startsWith("hub:") && <Pill tone="muted">on {approval.device}</Pill>}
        {approval.device?.startsWith("hub:") && <Pill tone="muted">asked by another device</Pill>}
      </div>
      <code className="block max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-panel px-2.5 py-2 font-mono text-[12.5px]">{detail}</code>
      {approval.warnings.length > 0 && (
        <ul className="list-disc pl-5 text-sm text-danger">
          {approval.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-1.5">
        {approval.scopes.map((s) => (
          <Button key={s} small variant={s === "once" ? "primary" : "default"} disabled={busy} onClick={() => decide(true, s)}>
            {SCOPE_LABEL[s] ?? s}
          </Button>
        ))}
        <Button small variant="danger" disabled={busy} onClick={() => decide(false)}>
          Don't allow
        </Button>
      </div>
      {approval.scopes.length > 1 && <p className="text-xs text-muted">Standing approvals cover “{approval.grant_key}”.</p>}
    </div>
  );
}
