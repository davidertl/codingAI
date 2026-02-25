import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { api } from '../api/client';

type AttemptEntry = {
  phase: string;
  attempt_index: number;
  status: string;
  timestamp?: string;
  payload?: Record<string, unknown>;
};

type IssueData = {
  number: number;
  title: string;
  state: string;
  body?: string;
  pipeline_stage?: string;
  attempts?: AttemptEntry[];
  diff?: string;
};

export function IssueDetail() {
  const { repo, number } = useParams<{ repo: string; number: string }>();
  const [data, setData] = useState<IssueData | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<'timeline' | 'diff'>('timeline');

  useEffect(() => {
    if (!repo || !number) return;
    setLoading(true);
    type PipelineData = { title?: string; state?: string; body?: string; pipeline_stage?: string; pipeline?: string; attempts?: AttemptEntry[] };
    type DiffData = { diff?: string };
    Promise.all([
      api.get<PipelineData>(`/pipeline/${repo}/${number}`).catch((): PipelineData => ({})),
      api.get<DiffData>(`/pipeline/${repo}/${number}/diff`).catch((): DiffData => ({ diff: '' })),
    ])
      .then(([pipeline, diffData]) => {
        setData({
          number: Number(number),
          title: String(pipeline.title || `Issue #${number}`),
          state: String(pipeline.state || 'unknown'),
          body: String(pipeline.body || ''),
          pipeline_stage: String(pipeline.pipeline_stage || pipeline.pipeline || ''),
          attempts: Array.isArray(pipeline.attempts) ? pipeline.attempts : [],
          diff: String(diffData?.diff || ''),
        });
      })
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [repo, number]);

  if (loading) return <div className="p-6 text-[var(--text-secondary)]">Loading...</div>;
  if (!data) return <div className="p-6 text-[var(--text-secondary)]">Issue not found.</div>;

  const attempts = data.attempts || [];

  return (
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      <div className="flex items-center gap-3 text-sm text-[var(--text-secondary)]">
        <Link to="/issues" className="hover:text-[var(--accent)]">Issues</Link>
        <span>/</span>
        <span>{repo}</span>
        <span>/</span>
        <span>#{number}</span>
      </div>

      <div>
        <h1 className="text-xl font-semibold">{data.title}</h1>
        <div className="flex gap-4 mt-2 text-sm text-[var(--text-secondary)]">
          <span>State: <span className="text-[var(--text-primary)]">{data.state}</span></span>
          {data.pipeline_stage && (
            <span>Stage: <span className="text-[var(--accent)]">{data.pipeline_stage}</span></span>
          )}
        </div>
      </div>

      <div className="flex gap-2 border-b border-[var(--border)]">
        <button
          onClick={() => setTab('timeline')}
          className={`px-4 py-2 text-sm border-b-2 transition-colors ${
            tab === 'timeline' ? 'border-[var(--accent)] text-[var(--accent)]' : 'border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
          }`}
        >
          Timeline ({attempts.length})
        </button>
        <button
          onClick={() => setTab('diff')}
          className={`px-4 py-2 text-sm border-b-2 transition-colors ${
            tab === 'diff' ? 'border-[var(--accent)] text-[var(--accent)]' : 'border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
          }`}
        >
          Diff
        </button>
      </div>

      {tab === 'timeline' && (
        <div className="space-y-3">
          {attempts.length === 0 ? (
            <p className="text-sm text-[var(--text-secondary)]">No attempts recorded.</p>
          ) : (
            attempts.map((a, i) => (
              <div key={i} className="flex items-start gap-3 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] p-4">
                <div className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${
                  a.status === 'ok' || a.status === 'passed' ? 'bg-[var(--success)]' : a.status === 'failed' ? 'bg-[var(--danger)]' : 'bg-[var(--warning)]'
                }`} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 text-sm">
                    <span className="font-medium">{a.phase}</span>
                    <span className="text-[var(--text-secondary)]">attempt {a.attempt_index}</span>
                    <span className={`text-xs px-1.5 py-0.5 rounded ${
                      a.status === 'ok' || a.status === 'passed'
                        ? 'bg-[var(--success)]/15 text-[var(--success)]'
                        : a.status === 'failed'
                        ? 'bg-[var(--danger)]/15 text-[var(--danger)]'
                        : 'bg-[var(--warning)]/15 text-[var(--warning)]'
                    }`}>
                      {a.status}
                    </span>
                  </div>
                  {a.timestamp && <p className="text-xs text-[var(--text-secondary)] mt-0.5">{a.timestamp}</p>}
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {tab === 'diff' && (
        <div className="rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] p-4">
          {data.diff ? (
            <pre className="text-xs font-mono whitespace-pre-wrap text-[var(--text-secondary)] overflow-auto max-h-[600px]">
              {data.diff}
            </pre>
          ) : (
            <p className="text-sm text-[var(--text-secondary)]">No diff available.</p>
          )}
        </div>
      )}
    </div>
  );
}
