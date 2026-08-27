export type Phase =
  | "new" | "spec" | "awaiting_spec_approval" | "plan" | "implement"
  | "integrate" | "gates" | "review" | "revise" | "done" | "escalated"
  | "failed" | "cancelled";

export type Status = "idle" | "running" | "blocked" | "done" | "failed";
export type CritStatus = "unknown" | "pass" | "fail";

export interface Usage {
  input_tokens: number; output_tokens: number; cache_read_tokens: number;
  cache_creation_tokens: number; cost_usd: number; turns: number; duration_s: number;
}

export interface Check { kind: "cmd" | "review" | "human"; cmd?: string | null; }

export interface Criterion {
  id: string; text: string; check: Check;
  lane?: string | null; status: CritStatus; note?: string | null;
}

export interface Finding {
  criterion?: string | null; file?: string | null; line?: number | null;
  problem: string; required_change: string; lane?: string | null;
  source: "critic" | "gate" | "human";
}

export interface Verdict {
  verdict: "pass" | "revise"; round: number; summary: string;
  findings: Finding[]; criteria_status: Record<string, CritStatus>; created_at: string;
}

export interface GateResult {
  name: string; cmd: string; lane?: string | null; exit_code: number;
  passed: boolean; duration_s: number; output_tail: string;
}

export interface LaneState {
  name: string; status: Status; session_id?: string | null;
  worktree?: string | null; branch?: string | null; last_error?: string | null;
}

export interface Task {
  id: string; title: string; description: string; repo: string;
  phase: Phase; status: Status; round: number; max_rounds: number;
  criteria_locked: boolean; branch?: string | null; base_branch?: string | null;
  lanes: Record<string, LaneState>; criteria: Criterion[];
  verdict?: Verdict | null; gate_results: GateResult[];
  pending_findings: Finding[]; usage: Usage; budget_usd?: number | null;
  archived: boolean;
  error?: string | null; parked_until?: string | null;
  created_at: string; updated_at: string;
}

export interface TaskDetail extends Task {
  running: boolean;
  spec: string | null;
  plan: string | null;
  reviews: Verdict[];
  feedback: { n: string; text: string }[];
  lane_docs: Record<string, string>;
}

export interface GodEvent {
  ts: string; task_id: string; kind: string; message: string;
  phase?: Phase | null; lane?: string | null;
  level: "info" | "warn" | "error"; data: Record<string, unknown>;
}

export interface RepoInfo {
  name: string; path: string; exists: boolean;
  onboarded: boolean; lanes: string[]; gates: string[];
}

export interface FsEntry {
  name: string; path: string;
  is_git: boolean; is_registered: boolean; is_onboarded: boolean; readable: boolean;
}

export interface FsListing {
  path: string; parent: string | null; entries: FsEntry[];
  is_git: boolean; is_registered: boolean; is_onboarded: boolean;
}

export interface PrunePlan {
  policy: { enabled: boolean; max_tasks: number; max_age_days: number };
  would_remove: { id: string; repo: string; title: string; reason: string; age_days: number }[];
}
