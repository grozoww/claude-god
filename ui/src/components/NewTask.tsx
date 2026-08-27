import { useState } from "react";
import { api } from "../api";
import type { RepoInfo } from "../types";
import { Modal } from "./bits";

export default function NewTask({
  repos, onClose, onCreated,
}: { repos: RepoInfo[]; onClose: () => void; onCreated: (id: string) => void }) {
  const [repo, setRepo] = useState(repos[0]?.name ?? "");
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [auto, setAuto] = useState(false);
  const [rounds, setRounds] = useState("");
  const [budget, setBudget] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    if (!title.trim() || !repo) return;
    setBusy(true); setErr(null);
    try {
      const t = await api.create({
        repo, title: title.trim(), description: desc.trim(),
        auto_approve_spec: auto,
        max_rounds: rounds ? Number(rounds) : null,
        budget_usd: budget ? Number(budget) : null,
      });
      onCreated(t.id);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
      setBusy(false);
    }
  }

  return (
    <Modal title="New task" onClose={onClose}>
      <div className="field">
        <label>Repo</label>
        <select value={repo} onChange={(e) => setRepo(e.target.value)}>
          {repos.map((r) => (
            <option key={r.name} value={r.name}>
              {r.name}{r.onboarded ? "" : "  (not onboarded)"}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label>What to build</label>
        <input
          autoFocus value={title} placeholder="add order refunds"
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && e.metaKey) void submit(); }}
        />
      </div>
      <div className="field">
        <label>Detail (optional)</label>
        <textarea rows={5} value={desc} onChange={(e) => setDesc(e.target.value)}
          placeholder="Constraints, links, edge cases the spec agent should know." />
      </div>
      <div className="grid2">
        <div className="field">
          <label>Max rounds (blank = project default)</label>
          <input value={rounds} onChange={(e) => setRounds(e.target.value)} placeholder="3" />
        </div>
        <div className="field">
          <label>Budget USD (blank = none)</label>
          <input value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="5.00" />
        </div>
      </div>
      <label className="check">
        <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
        <span>Skip spec approval <span className="muted">— cheaper to approve first</span></span>
      </label>
      {err && <div className="err">{err}</div>}
      <div className="actions">
        <button onClick={onClose}>Cancel</button>
        <button className="primary" disabled={busy || !title.trim() || !repo} onClick={submit}>
          {busy ? "Starting…" : "Start"}
        </button>
      </div>
    </Modal>
  );
}
