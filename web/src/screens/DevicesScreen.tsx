import { Bell, Globe, Info, Laptop, Monitor, Smartphone, Wifi, WifiOff } from "lucide-react";
import { useState } from "react";
import { api, Device, timeAgo } from "../api";
import { Button, Card, Empty, Field, Input, Page, Pill } from "../components/ui";
import { useStore } from "../store";

function SignIn() {
  const { state, toast, refreshState } = useStore();
  const [url, setUrl] = useState(state?.config.hub.url || "");
  const [identifier, setIdentifier] = useState("");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(false);
  return (
    <Card className="flex flex-col gap-3">
      <p className="text-sm text-muted">
        Sign in to a Nemo relay (yours, or a community one) and every device on the same account can hand tasks to the others. Approvals come
        back to whichever device is in your hand.
      </p>
      <Field label="Relay URL">
        <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://relay.example.com" />
      </Field>
      <Field label="E-mail, or a +86 mobile number">
        <Input value={identifier} onChange={(e) => setIdentifier(e.target.value)} placeholder="you@example.com" />
      </Field>
      {sent && (
        <Field label="Code">
          <Input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" placeholder="123456" />
        </Field>
      )}
      <div className="flex gap-2">
        {!sent ? (
          <Button
            variant="primary"
            disabled={!url || !identifier}
            onClick={() =>
              api("/api/account/code", { body: { url, identifier } })
                .then((r: any) => {
                  setSent(true);
                  toast(`Code sent to ${r.hint}.`);
                })
                .catch((e) => toast(e.message, "error"))
            }
          >
            Send code
          </Button>
        ) : (
          <Button
            variant="primary"
            disabled={!code}
            onClick={() =>
              api("/api/account/verify", { body: { url, identifier, code } })
                .then(() => {
                  toast("Signed in. Connecting…");
                  setTimeout(refreshState, 1500);
                })
                .catch((e) => toast(e.message, "error"))
            }
          >
            Sign in
          </Button>
        )}
      </div>
    </Card>
  );
}

const ICON = { phone: Smartphone, computer: Laptop, web: Globe } as Record<string, typeof Laptop>;

function DeviceCard({ d, self }: { d: Device; self: boolean }) {
  const { toast } = useStore();
  const [shot, setShot] = useState<string | null>(null);
  const [info, setInfo] = useState<any>(null);
  const Icon = ICON[d.kind] ?? Monitor;
  const call = async (action: string, args: object = {}) => {
    const r: any = await api(`/api/devices/${d.id}/call`, { body: { action, args } }).catch((e) => ({ ok: false, error: e.message }));
    if (!r.ok) toast(r.error || "Failed", "error");
    return r;
  };
  return (
    <Card className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <div className={`flex h-11 w-11 items-center justify-center rounded-xl ${d.online ? "bg-accent-soft text-accent" : "bg-code text-muted"}`}>
          <Icon size={22} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 font-semibold">
            {d.name} {self && <Pill tone="muted">this device</Pill>}
          </div>
          <div className="truncate text-[13px] text-muted">
            {d.os || d.kind} {d.version && `· Nemo ${d.version}`}
          </div>
        </div>
        {d.online ? (
          <Pill tone="ok">
            <Wifi size={12} /> online
          </Pill>
        ) : (
          <Pill tone="muted">
            <WifiOff size={12} /> {d.last_seen ? timeAgo(d.last_seen) : "offline"}
          </Pill>
        )}
      </div>
      {d.online && !self && (
        <div className="flex flex-wrap gap-1.5">
          <Button small onClick={async () => setInfo(await call("info"))}>
            <Info size={13} /> Info
          </Button>
          {d.actions.includes("screen") && (
            <Button small onClick={async () => {
              const r = await call("screen");
              if (r.ok) setShot(`data:${r.mime};base64,${r.image}`);
            }}>
              <Monitor size={13} /> Screen
            </Button>
          )}
          <Button small onClick={async () => (await call("notify", { title: "Nemo", body: "Hello from another device 👋" })).ok && toast("Sent.")}>
            <Bell size={13} /> Ping
          </Button>
        </div>
      )}
      {d.online && !self && <p className="text-xs text-muted">To have it do something, start a message with @{d.name}.</p>}
      {info && <pre className="overflow-auto rounded-lg bg-code p-2 font-mono text-xs">{JSON.stringify(info, null, 2)}</pre>}
      {shot && <img src={shot} alt="screen" className="rounded-lg border border-line" />}
    </Card>
  );
}

export function DevicesScreen() {
  const { hub, state, toast, refreshState } = useStore();
  return (
    <Page
      title="Devices"
      subtitle={hub?.configured ? `Signed in to ${hub.url}` : "Every device you own, one account."}
      actions={
        hub?.configured && (
          <Button small variant="ghost" onClick={() => confirm("Sign this device out of the relay?") && api("/api/account/signout", { body: {} }).then(refreshState).catch((e) => toast(e.message, "error"))}>
            Sign out
          </Button>
        )
      }
    >
      {!hub?.configured ? (
        <SignIn />
      ) : (
        <>
          {!hub.online && <Empty>Connecting to the hub… {hub.error}</Empty>}
          {hub.devices.length === 0 && hub.online && <Empty>No devices yet.</Empty>}
          {hub.devices.map((d) => (
            <DeviceCard key={d.id} d={d} self={d.id === state?.config.hub.device_id} />
          ))}
        </>
      )}
    </Page>
  );
}
