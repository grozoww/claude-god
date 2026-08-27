import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { FsEntry, FsListing } from "../types";
import { Modal, Pill } from "./bits";

/**
 * A browser cannot hand back a real filesystem path — `webkitdirectory` gives
 * file names only. The server runs on the same machine, so it does the walking
 * and this just renders what it finds.
 */
export default function FolderPicker({
  onClose, onPick,
}: { onClose: () => void; onPick: (path: string) => void }) {
  const [listing, setListing] = useState<FsListing | null>(null);
  const [roots, setRoots] = useState<FsEntry[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const go = useCallback(async (path: string) => {
    setBusy(true); setErr(null);
    try { setListing(await api.fsList(path)); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }, []);

  useEffect(() => {
    void api.fsRoots().then((r) => {
      setRoots(r);
      if (r.length) void go(r[0].path);
    }).catch((e) => setErr(String(e)));
  }, [go]);

  const cur = listing;
  const crumbs = cur ? cur.path.split("/").filter(Boolean) : [];

  return (
    <Modal title="Choose a folder" onClose={onClose}>
      <div className="row small" style={{ marginBottom: 8, flexWrap: "wrap" }}>
        {roots.map((r) => (
          <button key={r.path} style={{ padding: "2px 8px", fontSize: 11.5 }}
            onClick={() => go(r.path)}>{r.name}</button>
        ))}
      </div>

      <div className="row small mono muted" style={{ marginBottom: 8, flexWrap: "wrap", gap: 2 }}>
        <button style={{ padding: "1px 7px", fontSize: 11.5 }}
          disabled={!cur?.parent || busy}
          onClick={() => cur?.parent && go(cur.parent)}>↑</button>
        <span>/</span>
        {crumbs.map((c, i) => (
          <span key={i}>
            <button style={{ padding: "1px 5px", fontSize: 11.5, border: "none", background: "none" }}
              onClick={() => go("/" + crumbs.slice(0, i + 1).join("/"))}>{c}</button>
            {i < crumbs.length - 1 && <span>/</span>}
          </span>
        ))}
      </div>

      {err && <div className="err">{err}</div>}

      <div className="picker">
        {cur?.entries.length === 0 && <div className="muted small" style={{ padding: 10 }}>
          No sub-folders here. Use this one, or go up.
        </div>}
        {cur?.entries.map((e) => (
          <div key={e.path}
            className={`pick-row${e.readable ? "" : " unreadable"}`}
            onDoubleClick={() => e.readable && go(e.path)}
            onClick={() => e.readable && go(e.path)}>
            <span className="muted">{e.is_git ? "◆" : "▸"}</span>
            <span style={{ flex: 1 }}>{e.name}</span>
            {e.is_registered && <Pill kind="done">registered</Pill>}
            {e.is_git && !e.is_registered && <Pill>git</Pill>}
            {e.is_onboarded && <Pill kind="lane">onboarded</Pill>}
          </div>
        ))}
      </div>

      {cur && (
        <div className="row small muted" style={{ marginTop: 8 }}>
          <span className="mono">{cur.path}</span>
          {cur.is_git ? <Pill>git repo</Pill>
            : <span className="err">not a git repo — ccgod needs one</span>}
        </div>
      )}

      <div className="actions">
        <button onClick={onClose}>Cancel</button>
        <button className="primary" disabled={!cur || busy}
          onClick={() => cur && onPick(cur.path)}>
          Use this folder
        </button>
      </div>
    </Modal>
  );
}
