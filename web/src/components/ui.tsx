// Small shared building blocks.
import { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

type Variant = "primary" | "ghost" | "danger" | "default";

export function Button({ variant = "default", small, className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; small?: boolean }) {
  const look = {
    primary: "bg-accent text-accent-ink border-accent hover:brightness-110",
    danger: "text-danger border-danger/60 hover:bg-danger/10",
    ghost: "border-transparent hover:bg-ink/5",
    default: "bg-panel border-line hover:bg-ink/5",
  }[variant];
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg border font-medium transition disabled:opacity-50 disabled:cursor-default cursor-pointer ${
        small ? "px-2.5 py-1 text-[13px]" : "px-3.5 py-2 text-sm"
      } ${look} ${className}`}
    />
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-xl border border-line bg-panel p-4 shadow-sm ${className}`}>{children}</div>;
}

export function Field({ label, hint, children, className = "" }: { label: string; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={`flex min-w-0 flex-1 flex-col gap-1 text-[13px] text-muted ${className}`}>
      {label}
      {children}
      {hint && <span className="text-xs text-muted/80">{hint}</span>}
    </label>
  );
}

const inputCls = "w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-accent";

export const Input = (props: InputHTMLAttributes<HTMLInputElement>) => <input {...props} className={`${inputCls} ${props.className ?? ""}`} />;
export const TextArea = (props: TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea {...props} className={`${inputCls} ${props.className ?? ""}`} />;
export const Select = (props: SelectHTMLAttributes<HTMLSelectElement>) => <select {...props} className={`${inputCls} ${props.className ?? ""}`} />;

export function Page({ title, subtitle, actions, children }: { title: string; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-5 pb-24 md:py-8">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1">
            <h1 className="text-xl font-semibold">{title}</h1>
            {subtitle && <p className="mt-0.5 text-sm text-muted">{subtitle}</p>}
          </div>
          {actions}
        </div>
        {children}
      </div>
    </div>
  );
}

export function Pill({ children, tone = "accent" }: { children: ReactNode; tone?: "accent" | "muted" | "warn" | "danger" | "ok" }) {
  const cls = {
    accent: "bg-accent-soft text-accent",
    muted: "bg-code text-muted",
    warn: "bg-warn-soft text-warn",
    danger: "bg-danger/10 text-danger",
    ok: "bg-ok/10 text-ok",
  }[tone];
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>{children}</span>;
}

export function Tabs<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map(([v, label]) => (
        <button
          key={v}
          onClick={() => onChange(v)}
          className={`rounded-full border px-3 py-1 text-sm cursor-pointer ${v === value ? "border-accent bg-accent text-accent-ink" : "border-line bg-panel hover:bg-ink/5"}`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-xl border border-dashed border-line px-4 py-6 text-center text-sm text-muted">{children}</p>;
}
