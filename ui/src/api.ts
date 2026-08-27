import type { FsListing, GodEvent, PrunePlan, RepoInfo, Task, TaskDetail } from "./types";

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text();
    let msg = body;
    try { msg = JSON.parse(body).detail ?? body; } catch { /* plain text */ }
    throw new Error(msg || `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

const post = (url: string, body?: unknown) =>
  fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const api = {
  health: () => fetch("/api/health").then(j<{ ok: boolean; claude_available: boolean; max_parallel: number }>),
  repos: () => fetch("/api/repos").then(j<RepoInfo[]>),
  addRepo: (path: string, name?: string) => post("/api/repos", { path, name }).then(j<{ ok: true }>),
  onboard: (repo: string, hint = "") => post("/api/onboard", { repo, hint }).then(j<{ summary: string }>),

  tasks: (archived: "false" | "true" | "all" = "false") =>
    fetch(`/api/tasks?archived=${archived}`).then(j<Task[]>),
  task: (id: string) => fetch(`/api/tasks/${id}`).then(j<TaskDetail>),
  events: (id: string) => fetch(`/api/tasks/${id}/events?limit=500`).then(j<GodEvent[]>),
  diff: (id: string) => fetch(`/api/tasks/${id}/diff`).then(j<{ diff: string; note?: string }>),

  create: (body: {
    repo: string; title: string; description?: string;
    auto_approve_spec?: boolean; max_rounds?: number | null; budget_usd?: number | null;
  }) => post("/api/tasks", { start: true, ...body }).then(j<Task>),

  start: (id: string) => post(`/api/tasks/${id}/start`).then(j<Task>),
  approve: (id: string) => post(`/api/tasks/${id}/approve`).then(j<Task>),
  redo: (id: string, note: string, lane?: string) => post(`/api/tasks/${id}/redo`, { note, lane }).then(j<Task>),
  cancel: (id: string) => post(`/api/tasks/${id}/cancel`).then(j<Task>),
  cleanup: (id: string) => post(`/api/tasks/${id}/cleanup`).then(j<Task>),
  saveSpec: (id: string, text: string) =>
    fetch(`/api/tasks/${id}/spec`, {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
    }).then(j<Task>),

  usage: () => fetch("/api/usage").then(j<{ tasks: number; total: Task["usage"] }>),

  remove: (id: string, branch = false) =>
    fetch(`/api/tasks/${id}?branch=${branch}`, { method: "DELETE" }).then(j<{ deleted: boolean }>),
  archive: (id: string, archived = true) =>
    post(`/api/tasks/${id}/archive?archived=${archived}`).then(j<Task>),

  clone: (url: string, into?: string, name?: string) =>
    post("/api/repos/clone", { url, into, name })
      .then(j<{ path: string; name: string; onboarded: boolean; next: string | null }>),

  fsRoots: () => fetch("/api/fs/roots").then(j<FsListing["entries"]>),
  fsList: (path: string) =>
    fetch(`/api/fs/list?path=${encodeURIComponent(path)}`).then(j<FsListing>),

  prunePlan: () => fetch("/api/maintenance/prune").then(j<PrunePlan>),
  prune: () => post("/api/maintenance/prune").then(j<{ removed: PrunePlan["would_remove"] }>),
};

/** Live event stream. Returns an unsubscribe function. */
export function subscribe(onEvent: (e: GodEvent) => void, taskId?: string): () => void {
  const url = taskId ? `/api/stream?task_id=${encodeURIComponent(taskId)}` : "/api/stream";
  const es = new EventSource(url);
  es.onmessage = (m) => {
    try { onEvent(JSON.parse(m.data) as GodEvent); } catch { /* ignore malformed frame */ }
  };
  return () => es.close();
}
