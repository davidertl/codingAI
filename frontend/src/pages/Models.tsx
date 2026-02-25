import { useEffect, useState } from 'react';
import { api, type RoutingEntry, type ModelInfo } from '../api/client';
import { useAppStore } from '../store/appStore';

const ROLES = ['classifier', 'planner', 'researcher', 'coder', 'reviewer', 'test_interpreter', 'judge'];

export function Models() {
  const { routing, setRouting } = useAppStore();
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.get<{ routing: Record<string, RoutingEntry> }>('/model-routing')
      .then((d) => setRouting(d.routing || {}))
      .catch(() => {});
    api.get<{ models: ModelInfo[] }>('/models/available')
      .then((d) => setModels(d.models || []))
      .catch(() => {});
  }, [setRouting]);

  function setModel(role: string, model: string) {
    setRouting({
      ...routing,
      [role]: { ...routing[role], model, provider: '' },
    });
  }

  function setEscalation(role: string, model: string) {
    setRouting({
      ...routing,
      [role]: { ...routing[role], escalation_model: model },
    });
  }

  async function save() {
    setSaving(true);
    try {
      await api.put('/model-routing', { routing });
    } catch { /* toast */ }
    setSaving(false);
  }

  const modelNames = [...new Set(models.map((m) => m.name))];

  return (
    <div className="p-6 space-y-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Model Routing</h1>
        <button
          onClick={save}
          disabled={saving}
          className="px-4 py-2 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--accent-hover)] disabled:opacity-40 transition-colors"
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>

      <p className="text-sm text-[var(--text-secondary)]">
        Assign models to each orchestrator role. Escalation models are used for high-complexity tasks.
      </p>

      <div className="rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)]">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-[var(--text-secondary)]">
              <th className="text-left px-4 py-3 font-medium">Role</th>
              <th className="text-left px-4 py-3 font-medium">Model</th>
              <th className="text-left px-4 py-3 font-medium">Escalation Model</th>
            </tr>
          </thead>
          <tbody>
            {ROLES.map((role) => {
              const entry = routing[role] || { model: '', provider: '' };
              return (
                <tr key={role} className="border-b border-[var(--border)] last:border-b-0">
                  <td className="px-4 py-3 font-medium capitalize">{role.replace('_', ' ')}</td>
                  <td className="px-4 py-3">
                    <select
                      value={entry.model || ''}
                      onChange={(e) => setModel(role, e.target.value)}
                      className="w-full bg-[var(--bg-primary)] border border-[var(--border)] rounded px-2 py-1.5 text-sm text-[var(--text-primary)]"
                    >
                      <option value="">Default</option>
                      {modelNames.map((m) => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </select>
                  </td>
                  <td className="px-4 py-3">
                    <select
                      value={entry.escalation_model || ''}
                      onChange={(e) => setEscalation(role, e.target.value)}
                      className="w-full bg-[var(--bg-primary)] border border-[var(--border)] rounded px-2 py-1.5 text-sm text-[var(--text-primary)]"
                    >
                      <option value="">None</option>
                      {modelNames.map((m) => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </select>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <section className="rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] p-5">
        <h2 className="text-sm font-medium text-[var(--text-secondary)] uppercase tracking-wider mb-3">Available Models</h2>
        {models.length === 0 ? (
          <p className="text-sm text-[var(--text-secondary)]">No models detected. Check LLM provider configuration.</p>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {models.map((m, i) => (
              <div key={i} className="rounded bg-[var(--bg-primary)] border border-[var(--border)] p-3">
                <p className="text-sm font-medium truncate">{m.name}</p>
                <p className="text-xs text-[var(--text-secondary)]">{m.provider}</p>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
