import { useCallback, useEffect, useState } from "react";
import { api, subscribe } from "./api";
import type { RepoInfo, Task } from "./types";
import FolderPicker from "./components/FolderPicker";
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
  // Selection lives in the URL so a task is linkable and survives a reload.
  const [selected, setSelected] = useState<string | null>(
    () => new URLSearchParams(location.search).get("task"),
  );
  const [newOpen, setNewOpen] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [reposOpen, setReposOpen] = useState(false);
  const [health, setHealth] = useState<{ claude_available: boolean; max_parallel: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [t, r] = await Promise.all([api.tasks(showArchived ? "all" : "false"), api.repos()]);
      setTasks(t); setRepos(r); setErr(null);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }, [showArchived]);

  useEffect(() => { void refresh(); void api.health().then(setHealth).catch(() => {}); }, [refresh]);

  useEffect(() => {
    const url = new URL(location.href);
    if (selected) url.searchParams.set("task", selected);
    else url.searchParams.delete("task");
    history.replaceState(null, "", url);
  }, [selected]);

  useEffect(() => {
    const onPop = () => setSelected(new URLSearchParams(location.search).get("task"));
    addEventListener("popstate", onPop);
    return () => removeEventListener("popstate", onPop);
  }, []);
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
        <button className={showArchived ? "on" : ""}
          onClick={() => setShowArchived((v) => !v)}
          title="Show archived tasks too">
          {showArchived ? "Hiding none" : "Archived"}
        </button>
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
              <div className="row head" title={t.title}>
                <Dot task={t} />
                <span className="title">{t.title}</span>
              </div>
              <div className="row small muted">
                <Pill kind={t.phase}>{t.phase.replace(/_/g, " ")}</Pill>
                {t.archived && <Pill>archived</Pill>}
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
            ? <TaskDetail id={selected} onChanged={refresh}
                onGone={() => { setSelected(null); void refresh(); }} />
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
  const [cloneUrl, setCloneUrl] = useState("");
  const [cloneInto, setCloneInto] = useState("");
  const [picking, setPicking] = useState<null | "add" | "clone">(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label); setErr(null); setNote(null);
    try { await fn(); onChanged(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  };

  return (
    <>
      <Modal title="Repos" onClose={onClose}>
        {repos.length === 0 && (
          <div className="muted small">No repos yet. Add a folder or clone one.</div>
        )}
        {repos.map((r) => (
          <div key={r.name} className="row"
            style={{ padding: "7px 0", borderTop: "1px solid var(--line)" }}>
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

        <div className="field" style={{ marginTop: 16 }}>
          <label>Add a folder already on disk</label>
          <div className="row">
            <input value={path} onChange={(e) => setPath(e.target.value)}
              placeholder="/Users/you/dev/my-app" />
            <button onClick={() => setPicking("add")}>Browse…</button>
            <button className="primary" disabled={!path.trim() || busy !== null}
              onClick={() => run("add", async () => { await api.addRepo(path.trim()); setPath(""); })}>
              Add
            </button>
          </div>
        </div>

        <div className="field">
          <label>Or clone one that is not on disk yet</label>
          <input value={cloneUrl} onChange={(e) => setCloneUrl(e.target.value)}
            placeholder="git@github.com:you/my-app.git" />
          <div className="row" style={{ marginTop: 6 }}>
            <input value={cloneInto} onChange={(e) => setCloneInto(e.target.value)}
              placeholder="clone into… (blank = ~/.ccgod/repos)" />
            <button onClick={() => setPicking("clone")}>Browse…</button>
            <button className="primary" disabled={!cloneUrl.trim() || busy !== null}
              onClick={() => run("clone", async () => {
                const res = await api.clone(cloneUrl.trim(), cloneInto.trim() || undefined);
                setCloneUrl(""); setCloneInto("");
                setNote(res.onboarded
                  ? `Cloned to ${res.path}.`
                  : `Cloned to ${res.path}. Onboard it above to let agents plan against it.`);
              })}>
              {busy === "clone" ? "Cloning…" : "Clone"}
            </button>
          </div>
          <div className="small muted" style={{ marginTop: 4 }}>
            Uses your existing git credentials. Private repos work if your SSH key or
            credential helper does.
          </div>
        </div>

        {err && <div className="err">{err}</div>}
        {note && <div className="small" style={{ color: "var(--green)" }}>{note}</div>}
        <div className="actions"><button onClick={onClose}>Close</button></div>
      </Modal>

      {picking && (
        <FolderPicker
          onClose={() => setPicking(null)}
          onPick={(p) => {
            if (picking === "add") setPath(p); else setCloneInto(p);
            setPicking(null);
          }}
        />
      )}
    </>
  );
}
