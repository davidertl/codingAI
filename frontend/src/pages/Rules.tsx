import { useEffect, useState } from 'react';
import { useAppStore } from '../store/appStore';
import { api } from '../api/client';

type RulesData = {
  global: { text: string; path: string };
  project: { text: string; path: string };
  worker: { text: string; path: string };
  effective: { text: string; repo: string; sources: unknown[] };
};

export function Rules() {
  const { selectedRepo, repos } = useAppStore();
  const [repo, setRepo] = useState(selectedRepo || repos[0]?.repo || '');
  const [rules, setRules] = useState<RulesData | null>(null);
  const [activeTab, setActiveTab] = useState<'global' | 'project' | 'worker' | 'effective'>('effective');
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  useEffect(() => {
    if (!repo) return;
    Promise.all([
      api.get<{ rules: string }>('/rules/global').then((d) => ({ text: d.rules || '', path: 'global' })).catch(() => ({ text: '', path: 'global' })),
      api.get<{ rules: string }>(`/rules/projects/${repo}`).then((d) => ({ text: d.rules || '', path: `projects/${repo}` })).catch(() => ({ text: '', path: `projects/${repo}` })),
      api.get<{ rules: string }>(`/rules/workers/${repo}`).then((d) => ({ text: d.rules || '', path: `workers/${repo}` })).catch(() => ({ text: '', path: `workers/${repo}` })),
      api.get<{ text: string; repo: string; sources: unknown[] }>(`/rules/effective?repo=${repo}`).catch(() => ({ text: '', repo, sources: [] })),
    ]).then(([global_, project, worker, effective]) => {
      const data: RulesData = {
        global: global_,
        project,
        worker,
        effective: { text: effective.text || '', repo: effective.repo || repo, sources: effective.sources || [] },
      };
      setRules(data);
      setDraft(data.effective.text || '');
    }).catch(() => setRules(null));
  }, [repo]);

  const currentText = rules
    ? activeTab === 'effective' ? rules.effective.text
    : activeTab === 'global' ? rules.global.text
    : activeTab === 'project' ? rules.project.text
    : rules.worker.text
    : '';

  async function saveRules() {
    const pathMap: Record<string, string> = {
      global: '/rules/global',
      project: `/rules/projects/${repo}`,
      worker: `/rules/workers/${repo}`,
    };
    const endpoint = pathMap[activeTab];
    if (!endpoint) return;
    try {
      await api.put(endpoint, { rules_markdown: draft });
      setEditing(false);
    } catch { /* toast */ }
  }

  const tabs = ['effective', 'global', 'project', 'worker'] as const;

  return (
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Rules</h1>
        <select
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          className="bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-3 py-1.5 text-sm text-[var(--text-primary)]"
        >
          {repos.map((r) => (
            <option key={r.repo} value={r.repo}>{r.repo}</option>
          ))}
        </select>
      </div>

      <div className="flex gap-2 border-b border-[var(--border)]">
        {tabs.map((t) => (
          <button
            key={t}
            onClick={() => { setActiveTab(t); setEditing(false); setDraft(currentText); }}
            className={`px-4 py-2 text-sm border-b-2 capitalize transition-colors ${
              activeTab === t
                ? 'border-[var(--accent)] text-[var(--accent)]'
                : 'border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)]">
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border)]">
          <span className="text-xs text-[var(--text-secondary)] uppercase tracking-wider">{activeTab} rules</span>
          {activeTab !== 'effective' && (
            editing ? (
              <div className="flex gap-2">
                <button onClick={() => setEditing(false)} className="px-3 py-1 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]">Cancel</button>
                <button onClick={saveRules} className="px-3 py-1 text-xs rounded bg-[var(--accent)] text-white hover:bg-[var(--accent-hover)]">Save</button>
              </div>
            ) : (
              <button
                onClick={() => { setEditing(true); setDraft(currentText); }}
                className="px-3 py-1 text-xs rounded bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              >
                Edit
              </button>
            )
          )}
        </div>
        {editing ? (
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="w-full min-h-[400px] p-4 bg-transparent text-sm font-mono text-[var(--text-primary)] resize-none focus:outline-none"
          />
        ) : (
          <pre className="p-4 text-sm font-mono whitespace-pre-wrap text-[var(--text-secondary)] min-h-[200px]">
            {currentText || 'No rules defined.'}
          </pre>
        )}
      </div>
    </div>
  );
}
