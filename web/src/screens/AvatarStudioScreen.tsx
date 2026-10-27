import { ImagePlus, Trash2, Wand2 } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { Button, Card, Field, Input, Page, TextArea } from "../components/ui";
import { useStore } from "../store";

const MOODS = ["neutral", "happy", "thinking", "sleepy", "surprised"] as const;
const HINT: Record<string, string> = {
  neutral: "resting, listening",
  happy: "a task went well",
  thinking: "working on something",
  sleepy: "idle at night",
  surprised: "something needs you",
};

function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}

export function AvatarStudioScreen() {
  const { state, refreshState, toast } = useStore();
  const faces = state?.avatar.faces ?? {};
  const [description, setDescription] = useState(state?.avatar.description ?? "");
  const [busy, setBusy] = useState<string | null>(null);
  const canGenerate = !!state?.config.llm.image_model;

  const upload = async (mood: string, file: File) => {
    setBusy(mood);
    try {
      await api(`/api/avatar/${mood}`, { method: "PUT", body: { image: await readFile(file) } });
      await refreshState();
    } catch (e: any) {
      toast(e.message, "error");
    }
    setBusy(null);
  };
  const generate = async (mood: string) => {
    setBusy(mood);
    try {
      await api("/api/avatar/generate", { body: { mood, prompt: description } });
      await refreshState();
    } catch (e: any) {
      toast(e.message, "error");
    }
    setBusy(null);
  };

  return (
    <Page title="Look" subtitle="Give Nemo a face. Five stills, one per mood; the app picks the one that fits.">
      <Card className="flex flex-col gap-3">
        <Field label="Describe the look" hint={canGenerate ? "Your image model draws each mood from this." : "Set an image model in Settings to draw moods; or upload pictures below."}>
          <TextArea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="a small teal nautilus with big curious eyes, soft 3D style" />
        </Field>
        <div className="flex gap-2">
          <Button onClick={() => api("/api/avatar", { method: "PUT", body: { description } }).then(refreshState)}>Save description</Button>
          {canGenerate && (
            <Button variant="primary" disabled={!!busy} onClick={async () => {
              for (const m of MOODS) await generate(m);
            }}>
              <Wand2 size={15} /> Draw all five
            </Button>
          )}
        </div>
      </Card>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {MOODS.map((m) => (
          <Card key={m} className="flex flex-col items-center gap-2 !p-3">
            <div className="relative aspect-square w-full overflow-hidden rounded-xl bg-code">
              <img src={faces[m] ?? "/nemo.svg"} alt={m} className={`h-full w-full object-cover ${faces[m] ? "" : "p-8 opacity-40"} ${busy === m ? "animate-pulse" : ""}`} />
            </div>
            <div className="text-center">
              <div className="font-medium capitalize">{m}</div>
              <div className="text-xs text-muted">{HINT[m]}</div>
            </div>
            <div className="flex gap-1.5">
              <label className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-line px-2.5 py-1 text-[13px] hover:bg-ink/5">
                <ImagePlus size={13} /> Upload
                <input type="file" accept="image/*" hidden onChange={(e) => e.target.files?.[0] && upload(m, e.target.files[0])} />
              </label>
              {canGenerate && (
                <Button small disabled={!!busy} onClick={() => generate(m)}>
                  <Wand2 size={13} />
                </Button>
              )}
              {faces[m] && (
                <Button small variant="ghost" onClick={() => api(`/api/avatar/${m}`, { method: "DELETE" }).then(refreshState)}>
                  <Trash2 size={13} />
                </Button>
              )}
            </div>
          </Card>
        ))}
      </div>
      <Field label="Emoji when no picture is set" className="max-w-40">
        <Input defaultValue={state?.avatar.emoji} onBlur={(e) => api("/api/avatar", { method: "PUT", body: { emoji: e.target.value } }).then(refreshState)} />
      </Field>
    </Page>
  );
}
