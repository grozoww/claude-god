import { useCallback, useEffect, useState } from "react";
import { api, subscribe } from "./api";
import type { RepoInfo, Task } from "./types";
import NewTask from "./components/NewTask";
import TaskDetail from "./components/TaskDetail";
import { Dot, Modal, Pill, ago, cost } from "./components/bits";

const NOTIFY = new Set<string>(["done", "escalated", "failed", "awaiting_spec_approval"]);
const NOTIFY_TEXT: Record<string, string> = {
  done: "Finished",
  escalated: "Out of rounds — needs you",
  failed: "Failed",
  awaiting_spec_approval: "Spec ready for approval",
};

function notify(phase: string, taskId: string) {
  try {
    if (Notification?.permission === "granted") {
      new Notification(NOTIFY_TEXT[phase] ?? phase, { body: taskId, tag: taskId });
    }
  } catch { /* not available in this browser */ }
}

export default function App() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [repos, setRepos] = useState<RepoInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [reposOpen, setReposOpen] = useState(false);
  const [health, setHealth] = useState<{ claude_available: boolean; max_parallel: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [t, r] = await Promise.all([api.tasks(), api.repos()]);
      setTasks(t); setRepos(r); setErr(null);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }, []);

  useEffect(() => { void refresh(); void api.health().then(setHealth).catch(() => {}); }, [refresh]);
  // One global stream keeps the list live without polling, and raises a desktop
  // notification when a task finishes or gets stuck.
  useEffect(() => subscribe((e) => {
    void refresh();
    if (e.kind === "state" && e.phase && NOTIFY.has(e.phase)) notify(e.phase, e.task_id);
  }), [refresh]);

  useEffect(() => { void Notification?.requestPermission?.().catch(() => {}); }, []);
  useEffect(() => {
    const t = setInterval(() => void refresh(), 15000); // catch anything the stream missed
    return () => clearInterval(t);
  }, [refresh]);

  const needsYou = tasks.filter(
    (t) => t.phase === "awaiting_spec_approval" || t.phase === "escalated",
  ).length;
  const spend = tasks.reduce((sum, t) => sum + t.usage.cost_usd, 0);

  return (
    <div className="app">
      <header>
        <h1>claude<span>-god</span></h1>
        {needsYou > 0 && <Pill kind="escalated">{needsYou} need you</Pill>}
        <span className="spacer" />
        {health && !health.claude_available && (
          <span className="err">claude binary not found</span>
        )}
        <span className="muted small">
          {tasks.filter((t) => t.status === "running").length} running
          {health ? ` · max ${health.max_parallel}` : ""}
          {spend > 0 ? ` · $${spend.toFixed(2)} total` : ""}
        </span>
        <button onClick={() => setReposOpen(true)}>Repos</button>
        <button className="primary" disabled={repos.length === 0}
          onClick={() => setNewOpen(true)}>New task</button>
      </header>

      <div className="cols">
        <div className="list">
          {err && <div className="err" style={{ padding: 10 }}>{err}</div>}
          {tasks.length === 0 && (
            <div className="empty">
              {repos.length === 0
                ? <>No repos yet.<br />Add one to get started.</>
                : <>No tasks yet.</>}
            </div>
          )}
          {tasks.map((t) => (
            <div key={t.id} className={`task-row ${t.id === selected ? "active" : ""}`}
              onClick={() => setSelected(t.id)}>
              <div className="row">
                <Dot task={t} />
                <span className="title">{t.title}</span>
              </div>
              <div className="row small muted">
                <Pill kind={t.phase}>{t.phase.replace(/_/g, " ")}</Pill>
                <span>{t.repo.split("/").pop()}</span>
                <span>r{t.round}/{t.max_rounds}</span>
                <span>{cost(t.usage)}</span>
                <span className="spacer" />
                <span>{ago(t.updated_at)}</span>
              </div>
            </div>
          ))}
        </div>

        <div className="detail">
          {selected
            ? <TaskDetail id={selected} onChanged={refresh} />
            : <div className="empty">Select a task.</div>}
        </div>
      </div>

      {newOpen && (
        <NewTask repos={repos} onClose={() => setNewOpen(false)}
          onCreated={(id) => { setNewOpen(false); setSelected(id); void refresh(); }} />
      )}
      {reposOpen && <Repos repos={repos} onClose={() => setReposOpen(false)} onChanged={refresh} />}
    </div>
  );
}

function Repos({
  repos, onClose, onChanged,
}: { repos: RepoInfo[]; onClose: () => void; onChanged: () => void }) {
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label); setErr(null);
    try { await fn(); onChanged(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  };

  return (
    <Modal title="Repos" onClose={onClose}>
      {repos.map((r) => (
        <div key={r.name} className="row" style={{ padding: "7px 0", borderTop: "1px solid var(--line)" }}>
          <div style={{ flex: 1 }}>
            <div><b>{r.name}</b> {!r.exists && <span className="err">missing</span>}</div>
            <div className="small muted mono">{r.path}</div>
            <div className="small muted">
              {r.onboarded ? `lanes: ${r.lanes.join(", ")}` : "not onboarded"}
            </div>
          </div>
          <button disabled={busy !== null}
            onClick={() => run(r.name, () => api.onboard(r.name))}>
            {busy === r.name ? "Running…" : r.onboarded ? "Refresh context" : "Onboard"}
          </button>
        </div>
      ))}
      <div className="field" style={{ marginTop: 14 }}>
        <label>Add a repo by path</label>
        <input value={path} onChange={(e) => setPath(e.target.value)}
          placeholder="/Users/you/dev/my-app" />
      </div>
      {err && <div className="err">{err}</div>}
      <div className="actions">
        <button onClick={onClose}>Close</button>
        <button className="primary" disabled={!path.trim() || busy !== null}
          onClick={() => run("add", async () => { await api.addRepo(path.trim()); setPath(""); })}>
          Add
        </button>
      </div>
    </Modal>
  );
}
