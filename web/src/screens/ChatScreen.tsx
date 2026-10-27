import { Brain, ChevronRight, Hand, Menu, Send, ShieldCheck, Square } from "lucide-react";
import { Fragment, KeyboardEvent, useEffect, useRef, useState } from "react";
import { api, Message, Thread } from "../api";
import { ApprovalCard } from "../components/ApprovalCard";
import { Avatar } from "../components/Avatar";
import { Markdown } from "../components/Markdown";
import { ToolCard } from "../components/ToolCard";
import { Button, Pill } from "../components/ui";
import { Block, useStore } from "../store";

function Think({ text, live }: { text: string; live?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-xl border border-line/70 bg-code/60 text-sm">
      <button onClick={() => setOpen(!open)} className="flex w-full cursor-pointer items-center gap-1.5 px-3 py-1.5 text-muted">
        <Brain size={14} className={live ? "animate-pulse" : ""} />
        {live ? "Thinking…" : "Thought it through"}
        <ChevronRight size={14} className={`ml-auto transition ${open ? "rotate-90" : ""}`} />
      </button>
      {open && <div className="whitespace-pre-wrap border-t border-line/70 px-3 py-2 text-[13px] text-muted">{text}</div>}
    </div>
  );
}

function AgentRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex gap-2.5">
      <div className="pt-0.5">
        <Avatar size={28} />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-2">{children}</div>
    </div>
  );
}

function History({ thread, running }: { thread: Thread; running: boolean }) {
  const results = new Map<string, Message>();
  for (const m of thread.messages) if (m.role === "tool" && m.tool_call_id) results.set(m.tool_call_id, m);
  const rows: React.ReactNode[] = [];
  let group: React.ReactNode[] = [];
  const flush = () => {
    if (group.length) rows.push(<AgentRow key={`g${rows.length}`}>{group}</AgentRow>);
    group = [];
  };
  for (const m of thread.messages) {
    if (m.role === "user") {
      flush();
      rows.push(
        <div key={m.id} className="flex justify-end">
          <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-accent px-3.5 py-2 text-accent-ink">{m.content}</div>
        </div>,
      );
    } else if (m.role === "assistant" && m.name === "event") {
      flush();
      rows.push(
        <div key={m.id} className={`self-center rounded-full border border-line bg-panel px-3 py-0.5 text-[13px] ${m.ok === false ? "text-danger" : "text-muted"}`}>
          {m.content}
        </div>,
      );
    } else if (m.role === "assistant") {
      group.push(
        <Fragment key={m.id}>
          {m.reasoning && <Think text={m.reasoning} />}
          {m.content && <Markdown text={m.content} />}
          {m.tool_calls.map((c) => {
            const r = results.get(c.id);
            const images = r?.attachments.map((_, i) => `/api/threads/${thread.id}/attachments/${r.id}/${i}`);
            const status = !r ? (running ? "run" : "err") : r.ok === false ? (r.content.startsWith("Refused by the Sentinel") ? "refused" : "err") : "ok";
            const summary = c.arguments.command ?? c.arguments.path ?? c.arguments.query ?? c.arguments.url ?? c.arguments.label ?? c.arguments.action ?? "";
            return <ToolCard key={c.id} name={c.name} summary={String(summary)} output={r?.content} status={status} images={images} />;
          })}
        </Fragment>,
      );
    }
  }
  flush();
  return <>{rows}</>;
}

function Live({ blocks }: { blocks: Block[] }) {
  const last = blocks[blocks.length - 1];
  const working = !last || (last.type === "tool" && last.status !== "run") || (last.type === "text" && last.done);
  return (
    <AgentRow>
      {blocks.map((b, i) => {
        if (b.type === "reasoning") return <Think key={i} text={b.text} live={i === blocks.length - 1} />;
        if (b.type === "text") return <Markdown key={i} text={b.text} streaming={!b.done} />;
        if (b.type === "tool") return <ToolCard key={b.id} name={b.name} summary={b.summary} output={b.output} status={b.status} />;
        return (
          <div key={i} className="rounded-lg border-l-2 border-accent/50 pl-2.5 text-sm text-muted">
            <span className="font-medium text-ink">{b.device}</span> {b.stage === "start" ? `is on it: ${b.text}` : b.text}
          </div>
        );
      })}
      {working && <div className="animate-pulse text-sm text-muted">Working…</div>}
    </AgentRow>
  );
}

const SUGGESTIONS = [
  "What's in my workspace folder? Summarise it.",
  "Search the web for today's top tech news and give me five bullet points.",
  "Every weekday at 9:00, brief me on the weather in my city.",
  "Remember that I prefer short answers and metric units.",
];

export function ChatScreen({ openDrawer }: { openDrawer: () => void }) {
  const { param, versions, live, running, approvals, takeovers, hands, state, go, toast, refreshThreads } = useStore();
  const [thread, setThread] = useState<Thread | null>(null);
  const [text, setText] = useState("");
  const [pending, setPending] = useState<string[]>([]);
  const scroller = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const tid = param;
  const isRunning = !!tid && running.has(tid);

  useEffect(() => {
    if (!tid) {
      setThread(null);
      return;
    }
    api<Thread>(`/api/threads/${tid}`)
      .then((t) => {
        setThread(t);
        // keep only optimistic bubbles the server has not recorded yet
        setPending((p) => p.filter((x) => !t.messages.some((m) => m.role === "user" && m.content === x)));
      })
      .catch(() => go("chat"));
  }, [tid, versions[tid], go]);

  const blocks = tid ? live[tid] : undefined;
  const mine = approvals.filter((a) => a.conversation_id === tid);
  useEffect(() => {
    const el = scroller.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 200) el.scrollTop = el.scrollHeight;
  }, [blocks, thread, mine.length, pending]);
  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [tid]);

  const send = async (value = text) => {
    const body = value.trim();
    if (!body) return;
    if (!state?.config.ready) {
      toast("Set up a model first.", "warn");
      return go("settings");
    }
    setText("");
    let id = tid;
    try {
      if (!id) {
        const created = await api<{ id: string }>("/api/threads", { body: {} });
        id = created.id;
        history.replaceState(null, "", `#/chat/${id}`);
        window.dispatchEvent(new HashChangeEvent("hashchange"));
        refreshThreads();
      }
      setPending((p) => [...p, body]);
      const r = await api<{ status: string }>(`/api/threads/${id}/messages`, { body: { text: body } });
      if (r.status === "queued") toast("Added to what Nemo is doing now.");
    } catch (e: any) {
      toast(e.message, "error");
      setPending((p) => p.filter((x) => x !== body));
    }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };

  const shown = thread && thread.id === tid ? thread : null;
  const empty = !shown?.messages.length && !pending.length && !blocks?.length;
  const hand = tid ? hands[tid] : undefined;

  return (
    <div className="flex h-full flex-col">
      <header className="flex min-h-14 items-center gap-2 border-b border-line px-4">
        <button className="cursor-pointer text-muted md:hidden" onClick={openDrawer}>
          <Menu size={20} />
        </button>
        <h1 className="min-w-0 flex-1 truncate font-semibold">{shown?.title ?? state?.config.name ?? "Nemo"}</h1>
        {shown && shown.tainted.length > 0 && (
          <span title={`Read private data: ${shown.tainted.join(", ")}. Sending data out will ask first.`}>
            <Pill tone="warn">
              <ShieldCheck size={12} /> private data in play
            </Pill>
          </span>
        )}
        {shown?.origin.startsWith("hub:") && <Pill tone="muted">from another device</Pill>}
      </header>

      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-5">
          {empty && (
            <div className="mx-auto mt-[8vh] flex max-w-lg flex-col items-center text-center">
              <Avatar size={72} mood="happy" />
              <h2 className="mt-3 text-2xl font-semibold">Hi, I'm {state?.config.name ?? "Nemo"}.</h2>
              <p className="mt-1.5 text-sm text-muted">
                {state?.config.ready ? (
                  "I do things on your devices: commands, files, the web, your apps. I ask before anything that can't be undone, and I remember you."
                ) : (
                  <>
                    First, give me a model to think with in{" "}
                    <a className="text-accent underline" href="#/settings">
                      Settings
                    </a>
                    .
                  </>
                )}
              </p>
              {state?.config.ready && (
                <div className="mt-5 grid w-full gap-2 text-left sm:grid-cols-2">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} onClick={() => send(s)} className="cursor-pointer rounded-xl border border-line bg-panel px-3 py-2.5 text-sm hover:border-accent">
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {shown && <History thread={shown} running={isRunning} />}
          {pending.map((p, i) => (
            <div key={`p${i}`} className="flex justify-end">
              <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-accent/80 px-3.5 py-2 text-accent-ink">{p}</div>
            </div>
          ))}
          {isRunning && blocks && <Live blocks={blocks} />}
          {mine.map((a) => (
            <ApprovalCard key={a.id} approval={a} />
          ))}
          {tid && takeovers[tid] && (
            <div className="flex flex-col gap-2 rounded-xl border border-accent/60 bg-accent-soft p-3.5">
              <div className="flex items-center gap-2 font-semibold">
                <Hand size={17} /> Your turn
              </div>
              <p className="text-sm">{takeovers[tid]}</p>
              <div>
                <Button variant="primary" small onClick={() => api(`/api/threads/${tid}/takeover`, { body: {} })}>
                  Done, carry on
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="mx-auto w-full max-w-3xl px-4 pb-4">
        {hand && <div className="mb-2 truncate text-xs text-muted">✋ {hand.label}</div>}
        <div className="flex items-end gap-2 rounded-2xl border border-line bg-panel p-1.5 shadow-sm focus-within:border-accent">
          <textarea
            ref={input}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = Math.min(e.target.scrollHeight, 200) + "px";
            }}
            onKeyDown={onKey}
            rows={1}
            placeholder={isRunning ? "Add to the task…" : "Ask Nemo to do something…"}
            className="max-h-[200px] min-h-[40px] flex-1 resize-none bg-transparent px-2.5 py-2 text-[15px] outline-none"
          />
          {isRunning && (
            <Button variant="danger" onClick={() => api(`/api/threads/${tid}/stop`, { body: {} })} title="Stop">
              <Square size={14} fill="currentColor" />
            </Button>
          )}
          <Button variant="primary" onClick={() => send()} disabled={!text.trim()} title="Send">
            <Send size={16} />
          </Button>
        </div>
      </div>
    </div>
  );
}
