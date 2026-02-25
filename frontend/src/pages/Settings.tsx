import { useEffect, useState } from 'react';
import { useAppStore } from '../store/appStore';
import { api, type Project } from '../api/client';

type SetupValues = {
  github_app_id?: string;
  github_pem_configured?: boolean;
  llm_provider?: string;
  target_repos?: string;
};

function InputField({ label, value, onChange, type = 'text', disabled = false }: {
  label: string; value: string; onChange: (v: string) => void; type?: string; disabled?: boolean;
}) {
  return (
    <label className="block">
      <span className="text-xs text-[var(--text-secondary)] uppercase tracking-wider">{label}</span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="mt-1 w-full bg-[var(--bg-primary)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] disabled:opacity-50 focus:outline-none focus:border-[var(--accent)]"
      />
    </label>
  );
}

function ProjectRow({ project, onToggle, onModeChange }: {
  project: Project; onToggle: () => void; onModeChange: (mode: string) => void;
}) {
  return (
    <div className="flex items-center justify-between py-3 px-4 border-b border-[var(--border)] last:border-b-0">
      <div className="flex items-center gap-3">
        <button
          onClick={onToggle}
          className={`w-8 h-5 rounded-full transition-colors flex items-center px-0.5 ${
            project.enabled ? 'bg-[var(--accent)]' : 'bg-[var(--bg-tertiary)]'
          }`}
        >
          <span className={`w-4 h-4 rounded-full bg-white transition-transform ${project.enabled ? 'translate-x-3' : ''}`} />
        </button>
        <span className="text-sm">{project.repo}</span>
      </div>
      <select
        value={project.push_gate_mode || 'auto_10s'}
        onChange={(e) => onModeChange(e.target.value)}
        className="bg-[var(--bg-primary)] border border-[var(--border)] rounded px-2 py-1 text-xs text-[var(--text-primary)]"
      >
        <option value="auto_10s">Auto (10s pause)</option>
        <option value="manual">Manual approval</option>
        <option value="auto_immediate">Auto immediate</option>
      </select>
    </div>
  );
}

export function Settings() {
  const { projects } = useAppStore();
  const [setup, setSetup] = useState<SetupValues>({});
  const [localProjects, setLocalProjects] = useState<Project[]>([]);

  useEffect(() => {
    api.get<SetupValues>('/setup/values').then(setSetup).catch(() => {});
  }, []);

  useEffect(() => {
    setLocalProjects(projects);
  }, [projects]);

  async function toggleProject(repo: string) {
    const p = localProjects.find((x) => x.repo === repo);
    if (!p) return;
    const newEnabled = !p.enabled;
    const updated = { ...p, enabled: newEnabled };
    setLocalProjects((prev) => prev.map((x) => (x.repo === repo ? updated : x)));
    try {
      await api.post(`/projects/${repo}/${newEnabled ? 'enable' : 'disable'}`);
    } catch { /* rollback */ }
  }

  async function changePushGate(repo: string, mode: string) {
    const p = localProjects.find((x) => x.repo === repo);
    if (!p) return;
    const updated = { ...p, push_gate_mode: mode };
    setLocalProjects((prev) => prev.map((x) => (x.repo === repo ? updated : x)));
    try {
      await api.post(`/projects/${repo}/push-gate?mode=${mode}`);
    } catch { /* rollback */ }
  }

  return (
    <div className="p-6 space-y-8 max-w-4xl mx-auto">
      <h1 className="text-xl font-semibold">Settings</h1>

      <section className="rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] p-5 space-y-4">
        <h2 className="text-sm font-medium text-[var(--text-secondary)] uppercase tracking-wider">Environment</h2>
        <div className="grid grid-cols-2 gap-4">
          <InputField label="GitHub App ID" value={setup.github_app_id || ''} onChange={() => {}} disabled />
          <InputField label="LLM Provider" value={setup.llm_provider || ''} onChange={() => {}} disabled />
          <InputField label="Target Repos" value={setup.target_repos || ''} onChange={() => {}} disabled />
          <div className="flex items-end">
            <span className={`text-sm ${setup.github_pem_configured ? 'text-[var(--success)]' : 'text-[var(--danger)]'}`}>
              PEM: {setup.github_pem_configured ? 'Configured' : 'Missing'}
            </span>
          </div>
        </div>
      </section>

      <section className="rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)]">
        <div className="px-5 py-3 border-b border-[var(--border)]">
          <h2 className="text-sm font-medium text-[var(--text-secondary)] uppercase tracking-wider">Projects</h2>
        </div>
        {localProjects.length === 0 ? (
          <p className="p-5 text-sm text-[var(--text-secondary)]">No projects configured.</p>
        ) : (
          localProjects.map((p) => (
            <ProjectRow
              key={p.repo}
              project={p}
              onToggle={() => toggleProject(p.repo)}
              onModeChange={(mode) => changePushGate(p.repo, mode)}
            />
          ))
        )}
      </section>
    </div>
  );
}
