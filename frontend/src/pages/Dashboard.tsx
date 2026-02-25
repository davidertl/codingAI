import { useAppStore } from '../store/appStore';
import { api } from '../api/client';

function StatusCard({ title, value, sub, color }: { title: string; value: string | number; sub?: string; color?: string }) {
  return (
    <div className="rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] p-4">
      <p className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-1">{title}</p>
      <p className={`text-2xl font-semibold ${color || 'text-[var(--text-primary)]'}`}>{value}</p>
      {sub && <p className="text-xs text-[var(--text-secondary)] mt-1">{sub}</p>}
    </div>
  );
}

function RepoRow({ repo, onRun, onStop }: { repo: { repo: string; enabled: boolean }; onRun: () => void; onStop: () => void }) {
  const workers = useAppStore((s) => s.workers);
  const w = workers[repo.repo];
  const running = w?.running ?? false;

  return (
    <div className="flex items-center justify-between py-3 px-4 border-b border-[var(--border)] last:border-b-0">
      <div className="flex items-center gap-3">
        <span className={`w-2 h-2 rounded-full ${running ? 'bg-[var(--success)]' : 'bg-[var(--text-secondary)]'}`} />
        <span className="text-sm font-medium">{repo.repo}</span>
      </div>
      <div className="flex gap-2">
        {running ? (
          <button
            onClick={onStop}
            className="px-3 py-1 text-xs rounded bg-[var(--danger)]/20 text-[var(--danger)] hover:bg-[var(--danger)]/30 transition-colors"
          >
            Stop
          </button>
        ) : (
          <button
            onClick={onRun}
            className="px-3 py-1 text-xs rounded bg-[var(--accent)]/20 text-[var(--accent)] hover:bg-[var(--accent)]/30 transition-colors"
          >
            Run
          </button>
        )}
      </div>
    </div>
  );
}

export function Dashboard() {
  const { repos, health, projects, workers, selectedRepo, setSelectedRepo } = useAppStore();
  const activeWorkers = Object.values(workers).filter((w) => w.running).length;
  const totalRepos = repos.length;
  const enabledProjects = projects.filter((p) => p.enabled).length;
  const uptime = health?.uptime_seconds ? Math.floor(health.uptime_seconds / 3600) : 0;

  async function runRepo(repo: string) {
    try { await api.post(`/run/repo/${repo}`); } catch { /* toast */ }
  }
  async function stopRepo(repo: string) {
    try { await api.post(`/stop/repo/${repo}`); } catch { /* toast */ }
  }

  return (
    <div className="p-6 space-y-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Dashboard</h1>
        <select
          value={selectedRepo || ''}
          onChange={(e) => setSelectedRepo(e.target.value || null)}
          className="bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-3 py-1.5 text-sm text-[var(--text-primary)]"
        >
          <option value="">All repos</option>
          {repos.map((r) => (
            <option key={r.repo} value={r.repo}>{r.repo}</option>
          ))}
        </select>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatusCard title="Repositories" value={totalRepos} />
        <StatusCard title="Active Workers" value={activeWorkers} color={activeWorkers > 0 ? 'text-[var(--success)]' : ''} />
        <StatusCard title="Projects" value={enabledProjects} sub={`of ${projects.length} total`} />
        <StatusCard title="Uptime" value={`${uptime}h`} sub={health?.llm?.provider || ''} />
      </div>

      <div className="rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)]">
        <div className="px-4 py-3 border-b border-[var(--border)]">
          <h2 className="text-sm font-medium text-[var(--text-secondary)]">Repositories</h2>
        </div>
        {repos.length === 0 ? (
          <p className="p-4 text-sm text-[var(--text-secondary)]">No repos configured.</p>
        ) : (
          repos.map((r) => (
            <RepoRow
              key={r.repo}
              repo={r}
              onRun={() => runRepo(r.repo)}
              onStop={() => stopRepo(r.repo)}
            />
          ))
        )}
      </div>
    </div>
  );
}
