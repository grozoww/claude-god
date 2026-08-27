import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, subscribe } from "../api";
import type { CritStatus, Finding, GodEvent, TaskDetail as TD } from "../types";
import { Diff, Modal, Pill, ProblemText, cost, findingLabel, tokens } from "./bits";

const MARK: Record<CritStatus, string> = { pass: "✔", fail: "✘", unknown: "·" };
type Tab = "overview" | "spec" | "plan" | "diff" | "log";

export default function TaskDetail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const [task, setTask] = useState<TD | null>(null);
  const [events, setEvents] = useState<GodEvent[]>([]);
  const [tab, setTab] = useState<Tab>(
    () => (new URLSearchParams(location.search).get("tab") as Tab) || "overview",
  );
  const [diff, setDiff] = useState<string>("");
  const [specDraft, setSpecDraft] = useState<string>("");
  const [redoOpen, setRedoOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const t = await api.task(id);
      setTask(t);
      setSpecDraft((prev) => (prev ? prev : t.spec ?? ""));
    } catch (e) { setErr(String(e)); }
  }, [id]);

  useEffect(() => {
    setTask(null); setEvents([]); setSpecDraft("");
    setTab((new URLSearchParams(location.search).get("tab") as Tab) || "overview");
  }, [id]);

  useEffect(() => {
    const url = new URL(location.href);
    if (tab === "overview") url.searchParams.delete("tab");
    else url.searchParams.set("tab", tab);
    history.replaceState(null, "", url);
  }, [tab]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void api.events(id).then(setEvents).catch(() => {}); }, [id]);

  // Live: every event for this task refreshes state and appends to the log.
  useEffect(() => subscribe((e) => {
    if (e.task_id !== id) return;
    setEvents((prev) => [...prev.slice(-800), e]);
    void load();
    onChanged();
  }, id), [id, load, onChanged]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [events, tab]);

  useEffect(() => {
    if (tab === "diff") void api.diff(id).then((d) => setDiff(d.diff || d.note || "(no diff)"));
  }, [tab, id]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); await load(); onChanged(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const findings = useMemo<Finding[]>(
    () => task?.pending_findings ?? [], [task],
  );

  if (!task) return <div className="empty">{err ?? "Loading…"}</div>;

  const awaiting = task.phase === "awaiting_spec_approval";
  const stuck = task.phase === "escalated" || task.phase === "failed";
  const idle = !task.running && task.status !== "running";

  return (
    <div>
      <div className="row" style={{ marginBottom: 4 }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>{task.title}</h2>
        <Pill kind={task.phase}>{task.phase.replace(/_/g, " ")}</Pill>
        {task.running && <Pill kind="running">running</Pill>}
        {task.criteria_locked && <Pill>criteria locked</Pill>}
      </div>
      <div className="row small muted mono" style={{ marginBottom: 12 }}>
        <span>{task.id}</span>
        <span>round {task.round}/{task.max_rounds}</span>
        <span>{task.branch ?? "no branch"}</span>
        <span>{cost(task.usage)} · {tokens(task.usage)} · {task.usage.turns} turns</span>
      </div>

      <div className="row" style={{ marginBottom: 14 }}>
        {awaiting && (
          <button className="primary" disabled={busy}
            onClick={() => act(async () => {
              if (specDraft && specDraft !== task.spec) await api.saveSpec(id, specDraft);
              await api.approve(id);
            })}>
            Approve spec & continue
          </button>
        )}
        {idle && !awaiting && task.phase !== "done" && task.phase !== "cancelled" && (
          <button disabled={busy} onClick={() => act(() => api.start(id))}>Resume</button>
        )}
        <button disabled={busy} onClick={() => setRedoOpen(true)}>
          {awaiting ? "Send spec back" : "Redo with note"}
        </button>
        {task.running && (
          <button className="danger" disabled={busy} onClick={() => act(() => api.cancel(id))}>
            Cancel
          </button>
        )}
        {Object.values(task.lanes).some((l) => l.worktree) && (
          <button disabled={busy} onClick={() => act(() => api.cleanup(id))}>
            Remove worktrees
          </button>
        )}
      </div>

      {err && <div className="err">{err}</div>}
      {task.error && <div className="err mono small">{task.error}</div>}
      {stuck && (
        <div className="card" style={{ borderColor: "#4b432c" }}>
          <h3>Needs you</h3>
          <p style={{ margin: 0 }}>
            {task.phase === "escalated"
              ? `Ran out of rounds (${task.max_rounds}). The findings below are what it could not fix.`
              : "The run failed. Fix the cause, then Resume — or send it back with a note."}
          </p>
        </div>
      )}

      <div className="tabs">
        {(["overview", "spec", "plan", "diff", "log"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>

      {tab === "overview" && (
        <>
          <div className="card">
            <h3>Acceptance criteria</h3>
            {task.criteria.length === 0 && <div className="muted">Not written yet.</div>}
            {task.criteria.map((c) => (
              <div className="crit" key={c.id}>
                <span className={`mark ${c.status}`}>{MARK[c.status]}</span>
                <div>
                  <div><b className="mono small">{c.id}</b> {c.text}</div>
                  <div className="small muted mono">
                    {c.check.kind === "cmd" ? `cmd ${c.check.cmd ?? "(missing)"}` : c.check.kind}
                    {c.lane ? ` · lane ${c.lane}` : ""}
                  </div>
                </div>
              </div>
            ))}
          </div>

          {findings.length > 0 && (
            <div className="card">
              <h3>Open findings ({findings.length})</h3>
              {findings.map((f, i) => (
                <div className={`finding ${f.source}`} key={i}>
                  <div className="small">
                    <b>{findingLabel(f)}</b>
                    {f.file && <span className="mono muted"> {f.file}{f.line ? `:${f.line}` : ""}</span>}
                    {f.criterion && <Pill>{f.source}</Pill>}
                  </div>
                  <div className="small"><ProblemText text={f.problem} /></div>
                  <div className="small muted">→ {f.required_change}</div>
                </div>
              ))}
            </div>
          )}

          {task.gate_results.length > 0 && (
            <div className="card">
              <h3>Gates</h3>
              {task.gate_results.map((g, i) => (
                <div key={i} style={{ marginBottom: 6 }}>
                  <div className="row small">
                    <span className={`mark ${g.passed ? "pass" : "fail"}`}
                      style={{ color: g.passed ? "var(--green)" : "var(--red)" }}>
                      {g.passed ? "✔" : "✘"}
                    </span>
                    <code className="mono">{g.cmd}</code>
                    <span className="muted">{g.duration_s}s</span>
                    {g.lane && <Pill kind="lane">{g.lane}</Pill>}
                  </div>
                  {!g.passed && <pre className="block" style={{ maxHeight: 160 }}>{g.output_tail}</pre>}
                </div>
              ))}
            </div>
          )}

          {task.reviews.length > 0 && (
            <div className="card">
              <h3>Review history</h3>
              {task.reviews.map((v) => (
                <div key={v.round} style={{ marginBottom: 10 }}>
                  <div className="row small">
                    <b>round {v.round}</b>
                    <Pill kind={v.verdict === "pass" ? "done" : "escalated"}>{v.verdict}</Pill>
                  </div>
                  <div className="small muted">{v.summary}</div>
                </div>
              ))}
            </div>
          )}

          {Object.keys(task.lanes).length > 0 && (
            <div className="card">
              <h3>Lanes</h3>
              {Object.values(task.lanes).map((l) => (
                <div key={l.name} className="row small" style={{ padding: "3px 0" }}>
                  <Pill kind="lane">{l.name}</Pill>
                  <span>{l.status}</span>
                  <span className="mono muted">{l.branch ?? "—"}</span>
                  {l.last_error && <span className="err">{l.last_error}</span>}
                </div>
              ))}
            </div>
          )}

          {task.feedback.length > 0 && (
            <div className="card">
              <h3>Your notes</h3>
              {task.feedback.map((f) => (
                <div key={f.n} className="finding human">
                  <div className="small" style={{ whiteSpace: "pre-wrap" }}>{f.text}</div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {tab === "spec" && (
        <div className="card">
          <h3>Spec {awaiting && <span className="muted">— editable until you approve</span>}</h3>
          {awaiting ? (
            <>
              <textarea rows={26} value={specDraft} onChange={(e) => setSpecDraft(e.target.value)} />
              <div className="actions">
                <button disabled={busy || specDraft === task.spec}
                  onClick={() => act(() => api.saveSpec(id, specDraft))}>Save</button>
              </div>
            </>
          ) : (
            <pre className="block">{task.spec ?? "Not written yet."}</pre>
          )}
        </div>
      )}

      {tab === "plan" && (
        <div className="card">
          <h3>Plan</h3>
          <pre className="block">{task.plan ?? "Not written yet."}</pre>
          {Object.entries(task.lane_docs).map(([name, text]) => (
            <div key={name}>
              <h3 style={{ marginTop: 14 }}>Lane brief: {name}</h3>
              <pre className="block">{text}</pre>
            </div>
          ))}
        </div>
      )}

      {tab === "diff" && (
        <div className="card">
          <h3>Diff vs {task.base_branch ?? "base"}</h3>
          <Diff text={diff} />
        </div>
      )}

      {tab === "log" && (
        <div className="card">
          <h3>Events</h3>
          <div className="log" ref={logRef}>
            {events.map((e, i) => (
              <div key={i} className={e.level}>
                <span className="ts">{new Date(e.ts).toLocaleTimeString()} </span>
                <span className="kind">{e.kind.padEnd(12)}</span>
                {e.lane ? `[${e.lane}] ` : ""}{e.message}
              </div>
            ))}
            {events.length === 0 && <div className="muted">No events yet.</div>}
          </div>
        </div>
      )}

      {redoOpen && (
        <RedoDialog
          awaiting={awaiting}
          lanes={Object.keys(task.lanes)}
          onClose={() => setRedoOpen(false)}
          onSubmit={async (note, lane) => {
            setRedoOpen(false);
            await act(() => api.redo(id, note, lane));
          }}
        />
      )}
    </div>
  );
}

function RedoDialog({
  awaiting, lanes, onClose, onSubmit,
}: {
  awaiting: boolean; lanes: string[];
  onClose: () => void; onSubmit: (note: string, lane?: string) => void;
}) {
  const [note, setNote] = useState("");
  const [lane, setLane] = useState("");
  return (
    <Modal title={awaiting ? "Send the spec back" : "Send it back"} onClose={onClose}>
      <div className="field">
        <label>
          {awaiting
            ? "What is wrong with the spec?"
            : "What is wrong? This enters the loop as a finding, same as the critic's."}
        </label>
        <textarea rows={6} autoFocus value={note} onChange={(e) => setNote(e.target.value)}
          placeholder="The 409 path returns the raw DB error instead of a clean message." />
      </div>
      {!awaiting && lanes.length > 1 && (
        <div className="field">
          <label>Lane (optional)</label>
          <select value={lane} onChange={(e) => setLane(e.target.value)}>
            <option value="">all lanes</option>
            {lanes.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        </div>
      )}
      <div className="actions">
        <button onClick={onClose}>Cancel</button>
        <button className="primary" disabled={!note.trim()}
          onClick={() => onSubmit(note.trim(), lane || undefined)}>Send back</button>
      </div>
    </Modal>
  );
}
