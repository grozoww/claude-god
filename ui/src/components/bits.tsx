import type { Task, Usage } from "../types";

export function Pill({ kind, children }: { kind?: string; children: React.ReactNode }) {
  return <span className={`pill ${kind ?? ""}`}>{children}</span>;
}

export function Dot({ task }: { task: Task }) {
  const cls = task.status === "running" ? "running"
    : task.phase === "done" ? "done"
    : task.phase === "failed" || task.phase === "cancelled" ? "failed"
    : task.status === "blocked" ? "blocked" : "";
  return <span className={`dot ${cls}`} />;
}

export function cost(u: Usage) {
  return `$${u.cost_usd.toFixed(2)}`;
}

export function tokens(u: Usage) {
  const k = (n: number) => (n >= 1000 ? `${Math.round(n / 1000)}k` : String(n));
  return `${k(u.input_tokens)} in / ${k(u.output_tokens)} out`;
}

export function ago(iso: string) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function Modal({
  title, children, onClose,
}: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}

/** Minimal diff colouring — enough to read, no library. */
export function Diff({ text }: { text: string }) {
  return (
    <pre className="block diff">
      {text.split("\n").map((line, i) => {
        const cls = line.startsWith("+") && !line.startsWith("+++") ? "add"
          : line.startsWith("-") && !line.startsWith("---") ? "del"
          : line.startsWith("@@") ? "hunk" : undefined;
        return <div key={i} className={cls}>{line || " "}</div>;
      })}
    </pre>
  );
}
