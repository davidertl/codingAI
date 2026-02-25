import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAppStore } from '../store/appStore';
import { api, type IssueEntry } from '../api/client';

const STAGE_COLORS: Record<string, string> = {
  passed: 'text-[var(--success)]',
  failed: 'text-[var(--danger)]',
  testing: 'text-[var(--warning)]',
  patching: 'text-[var(--accent)]',
  orchestrating: 'text-[var(--accent)]',
};

export function Issues() {
  const { selectedRepo, repos } = useAppStore();
  const [issues, setIssues] = useState<IssueEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [filterRepo, setFilterRepo] = useState(selectedRepo || '');

  useEffect(() => {
    if (!filterRepo && repos.length > 0) {
      setFilterRepo(repos[0].repo);
    }
  }, [repos, filterRepo]);

  useEffect(() => {
    if (!filterRepo) return;
    setLoading(true);
    api.get<{ queue?: { issues?: IssueEntry[] }; tracked_issues?: Array<Record<string, unknown>> }>(`/repo/${filterRepo}/summary`)
      .then((d) => {
        const queueIssues = d.queue?.issues || [];
        const tracked = d.tracked_issues || [];
        const trackedMap = new Map(
          tracked.map((t) => [Number(t.issue_number || 0), t])
        );
        const merged: IssueEntry[] = queueIssues.map((qi) => {
          const t = trackedMap.get(qi.number);
          return {
            ...qi,
            pipeline_stage: String(t?.pipeline || qi.pipeline_stage || ''),
            attempts_count: Number(t?.attempts_count ?? qi.attempts_count ?? 0),
          };
        });
        setIssues(merged);
      })
      .catch(() => setIssues([]))
      .finally(() => setLoading(false));
  }, [filterRepo]);

  return (
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Issues</h1>
        <select
          value={filterRepo}
          onChange={(e) => setFilterRepo(e.target.value)}
          className="bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-3 py-1.5 text-sm text-[var(--text-primary)]"
        >
          {repos.map((r) => (
            <option key={r.repo} value={r.repo}>{r.repo}</option>
          ))}
        </select>
      </div>

      <div className="rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)]">
        {loading ? (
          <p className="p-6 text-sm text-[var(--text-secondary)]">Loading...</p>
        ) : issues.length === 0 ? (
          <p className="p-6 text-sm text-[var(--text-secondary)]">No issues found.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-[var(--text-secondary)]">
                <th className="text-left px-4 py-3 font-medium">#</th>
                <th className="text-left px-4 py-3 font-medium">Title</th>
                <th className="text-left px-4 py-3 font-medium">Stage</th>
                <th className="text-left px-4 py-3 font-medium">Attempts</th>
              </tr>
            </thead>
            <tbody>
              {issues.map((issue) => (
                <tr key={issue.number} className="border-b border-[var(--border)] last:border-b-0 hover:bg-[var(--bg-tertiary)]/40 transition-colors">
                  <td className="px-4 py-3 text-[var(--text-secondary)]">
                    <Link to={`/issues/${filterRepo}/${issue.number}`} className="hover:text-[var(--accent)]">
                      #{issue.number}
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <Link to={`/issues/${filterRepo}/${issue.number}`} className="hover:text-[var(--accent)]">
                      {issue.title}
                    </Link>
                  </td>
                  <td className={`px-4 py-3 ${STAGE_COLORS[issue.pipeline_stage || ''] || 'text-[var(--text-secondary)]'}`}>
                    {issue.pipeline_stage || '-'}
                  </td>
                  <td className="px-4 py-3 text-[var(--text-secondary)]">{issue.attempts_count ?? '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
