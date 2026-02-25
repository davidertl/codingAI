const BASE_URL = import.meta.env.VITE_API_URL || '/api';
const WS_URL = import.meta.env.VITE_WS_URL || `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws`;

async function request<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem('codingai_token');
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(opts.headers as Record<string, string> || {}),
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(`${BASE_URL}${path}`, { ...opts, headers });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`API ${res.status}: ${text.slice(0, 300)}`);
  }
  return res.json();
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};

export type HealthResponse = {
  status: string;
  uptime_seconds: number;
  llm?: { provider: string; status: string };
};

export type Repo = {
  repo: string;
  enabled: boolean;
  issues_count?: number;
  last_run?: string;
};

export type Project = {
  repo: string;
  enabled: boolean;
  push_gate_mode: string;
};

export type Worker = {
  repo: string;
  running: boolean;
  started_at?: string;
};

export type IssueEntry = {
  number: number;
  title: string;
  state: string;
  pipeline_stage?: string;
  attempts_count?: number;
  last_attempt?: string;
};

export type RoutingEntry = {
  model: string;
  provider: string;
  escalation_model?: string;
};

export type ModelInfo = {
  name: string;
  provider: string;
  size: number;
};

export type ChatMessage = {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp?: string;
};

export type UsageSummary = {
  total_tokens: number;
  total_cost_usd: number;
  by_role: Record<string, { tokens: number; cost_usd: number }>;
};

export type LiveSnapshot = {
  type: string;
  time_utc: string;
  selected_repo?: string;
  health: HealthResponse;
  repos: Repo[];
  projects: Project[];
  workers: Record<string, Worker>;
  summary?: Record<string, unknown>;
  rules?: Record<string, unknown>;
  seq?: number;
};

export function createLiveSocket(
  onSnapshot: (data: LiveSnapshot) => void,
  onError?: (err: Event) => void,
): { send: (msg: unknown) => void; close: () => void } {
  const ws = new WebSocket(`${WS_URL}/live`);
  ws.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (data.type === 'snapshot') onSnapshot(data);
    } catch { /* ignore */ }
  };
  ws.onerror = (ev) => onError?.(ev);
  ws.onclose = () => {
    setTimeout(() => {
      const retry = createLiveSocket(onSnapshot, onError);
      Object.assign(handle, retry);
    }, 3000);
  };
  const handle = {
    send: (msg: unknown) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
    },
    close: () => ws.close(),
  };
  return handle;
}

export function createSSEStream(
  path: string,
  onMessage: (data: unknown) => void,
  onError?: (err: Event) => void,
): EventSource {
  const url = `${BASE_URL}${path}`;
  const es = new EventSource(url);
  es.onmessage = (ev) => {
    try { onMessage(JSON.parse(ev.data)); } catch { /* ignore */ }
  };
  if (onError) es.onerror = onError;
  return es;
}
