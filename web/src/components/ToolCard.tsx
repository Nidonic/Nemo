import { Ban, Check, ChevronRight, Loader2, X } from "lucide-react";
import { useState } from "react";
import { toolLabel } from "../api";

export type ToolStatus = "run" | "ok" | "err" | "refused";

export function ToolCard({ name, summary, output, status, images }: {
  name: string;
  summary: string;
  output?: string;
  status: ToolStatus;
  images?: string[];
}) {
  const [open, setOpen] = useState(false);
  const icon = {
    run: <Loader2 size={15} className="animate-spin text-accent" />,
    ok: <Check size={15} className="text-ok" />,
    err: <X size={15} className="text-danger" />,
    refused: <Ban size={15} className="text-warn" />,
  }[status];
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-panel text-[13.5px]">
      <button onClick={() => setOpen(!open)} className="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left">
        <ChevronRight size={14} className={`shrink-0 text-muted transition ${open ? "rotate-90" : ""}`} />
        <span className="shrink-0 font-semibold">{toolLabel(name)}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-muted">{summary}</span>
        {icon}
      </button>
      {images && images.length > 0 && (
        <div className="flex gap-2 overflow-x-auto border-t border-line px-3 py-2">
          {images.map((src) => (
            <a key={src} href={src} target="_blank" rel="noopener">
              <img src={src} alt="screenshot" className="h-40 rounded-lg border border-line object-contain" />
            </a>
          ))}
        </div>
      )}
      {open && output !== undefined && (
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words border-t border-line bg-code px-3 py-2.5 font-mono text-[12.5px]">{output}</pre>
      )}
    </div>
  );
}
