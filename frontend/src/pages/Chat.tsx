import { useState, useRef, useEffect } from 'react';
import { useAppStore } from '../store/appStore';
import { api, type ChatMessage } from '../api/client';
import { PipelineLog } from '../components/PipelineLog';

type TaskState = {
  taskId: string;
  status: string;
  plan: Record<string, unknown>;
  classification: Record<string, unknown>;
  repo: string;
};

function PlanConfirmation({ task, onConfirm, onCancel }: {
  task: TaskState; onConfirm: () => void; onCancel: () => void;
}) {
  const plan = task.plan || {};
  const steps = (plan.steps as Array<Record<string, string>>) || [];
  const classification = task.classification || {};

  return (
    <div className="rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-medium">Execution Plan</h3>
        <span className="text-xs px-2 py-1 rounded bg-[var(--accent)]/15 text-[var(--accent)]">
          {String(classification.task_type || 'feature').toUpperCase()} / {String(classification.complexity || 'medium')}
        </span>
      </div>

      <p className="text-sm text-[var(--text-secondary)]">{String(plan.summary || '')}</p>

      {steps.length > 0 && (
        <div className="space-y-2">
          {steps.map((step, i) => (
            <div key={i} className="flex items-start gap-2 text-sm">
              <span className="text-[var(--text-secondary)] w-5 text-right flex-shrink-0">{i + 1}.</span>
              <div>
                <span className="font-medium">{step.title}</span>
                <span className="text-[var(--text-secondary)] ml-2">({step.owner_role})</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {plan.rollback_strategy ? (
        <p className="text-xs text-[var(--text-secondary)]">
          Rollback: {String(plan.rollback_strategy)}
        </p>
      ) : null}

      <div className="flex gap-3 pt-2">
        <button
          onClick={onConfirm}
          className="px-4 py-2 rounded-lg bg-[var(--success)] text-white text-sm font-medium hover:bg-[var(--success)]/80 transition-colors"
        >
          Confirm & Execute
        </button>
        <button
          onClick={onCancel}
          className="px-4 py-2 rounded-lg bg-[var(--bg-tertiary)] text-[var(--text-secondary)] text-sm hover:text-[var(--text-primary)] transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function DiffViewer({ diff }: { diff: string }) {
  if (!diff) return null;
  return (
    <div className="rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)]">
      <div className="px-4 py-2 border-b border-[var(--border)]">
        <span className="text-xs text-[var(--text-secondary)] uppercase tracking-wider">Diff</span>
      </div>
      <pre className="p-4 text-xs font-mono whitespace-pre-wrap overflow-auto max-h-[400px]">
        {diff.split('\n').map((line, i) => {
          let color = 'text-[var(--text-secondary)]';
          if (line.startsWith('+') && !line.startsWith('+++')) color = 'text-[var(--success)]';
          else if (line.startsWith('-') && !line.startsWith('---')) color = 'text-[var(--danger)]';
          else if (line.startsWith('@@')) color = 'text-[var(--accent)]';
          return <div key={i} className={color}>{line}</div>;
        })}
      </pre>
    </div>
  );
}

export function Chat() {
  const { chatMessages, addChatMessage, selectedRepo, repos } = useAppStore();
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [repo, setRepo] = useState(selectedRepo || repos[0]?.repo || '');
  const [activeTask, setActiveTask] = useState<TaskState | null>(null);
  const [taskDiff, setTaskDiff] = useState('');
  const [mode, setMode] = useState<'chat' | 'task'>('chat');
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatMessages]);

  async function send() {
    const text = input.trim();
    if (!text || sending) return;
    setInput('');
    const userMsg: ChatMessage = { role: 'user', content: text, timestamp: new Date().toISOString() };
    addChatMessage(userMsg);
    setSending(true);

    if (mode === 'task') {
      try {
        const res = await api.post<{ task_id: string; status: string; plan: Record<string, unknown>; classification: Record<string, unknown> }>(
          '/chat/task', { repo, message: text, history: chatMessages.slice(-10) }
        );
        setActiveTask({
          taskId: res.task_id,
          status: res.status,
          plan: res.plan || {},
          classification: res.classification || {},
          repo,
        });
        addChatMessage({
          role: 'assistant',
          content: 'I\'ve created an execution plan for your task. Please review it below.',
          timestamp: new Date().toISOString(),
        });
      } catch (e) {
        addChatMessage({
          role: 'assistant',
          content: `Error creating task: ${e instanceof Error ? e.message : 'Unknown'}`,
          timestamp: new Date().toISOString(),
        });
      }
    } else {
      try {
        const res = await api.post<{ reply: string }>('/chat/message', {
          repo, message: text, history: chatMessages.slice(-20),
        });
        addChatMessage({
          role: 'assistant',
          content: res.reply || 'No response.',
          timestamp: new Date().toISOString(),
        });
      } catch (e) {
        addChatMessage({
          role: 'assistant',
          content: `Error: ${e instanceof Error ? e.message : 'Unknown'}`,
          timestamp: new Date().toISOString(),
        });
      }
    }
    setSending(false);
  }

  async function confirmTask() {
    if (!activeTask) return;
    try {
      await api.post(`/chat/task/${activeTask.taskId}/confirm`);
      setActiveTask({ ...activeTask, status: 'executing' });
      addChatMessage({
        role: 'assistant',
        content: 'Task confirmed. Execution started...',
        timestamp: new Date().toISOString(),
      });
    } catch (e) {
      addChatMessage({
        role: 'assistant',
        content: `Error: ${e instanceof Error ? e.message : 'Unknown'}`,
        timestamp: new Date().toISOString(),
      });
    }
  }

  async function cancelTask() {
    if (!activeTask) return;
    try {
      await api.post(`/chat/task/${activeTask.taskId}/cancel`);
      setActiveTask(null);
      addChatMessage({
        role: 'assistant',
        content: 'Task cancelled.',
        timestamp: new Date().toISOString(),
      });
    } catch { /* ignore */ }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-3 px-6 py-3 border-b border-[var(--border)]">
        <h1 className="text-lg font-semibold">Chat</h1>
        <select
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          className="bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-2 py-1 text-sm text-[var(--text-primary)]"
        >
          {repos.map((r) => (
            <option key={r.repo} value={r.repo}>{r.repo}</option>
          ))}
        </select>
        <div className="flex rounded-lg border border-[var(--border)] text-xs overflow-hidden ml-auto">
          <button
            onClick={() => setMode('chat')}
            className={`px-3 py-1.5 transition-colors ${mode === 'chat' ? 'bg-[var(--accent)] text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}
          >
            Chat
          </button>
          <button
            onClick={() => setMode('task')}
            className={`px-3 py-1.5 transition-colors ${mode === 'task' ? 'bg-[var(--accent)] text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}
          >
            Code Task
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-6 space-y-4">
        {chatMessages.length === 0 && (
          <div className="text-center py-20 text-[var(--text-secondary)]">
            <p className="text-lg mb-2">Chat with CodingAI</p>
            <p className="text-sm">
              {mode === 'task'
                ? 'Describe a coding task. A plan will be generated for your confirmation before execution.'
                : 'Ask questions about your codebase or describe a task.'}
            </p>
          </div>
        )}

        {chatMessages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[70%] rounded-lg px-4 py-3 text-sm whitespace-pre-wrap ${
                msg.role === 'user'
                  ? 'bg-[var(--accent)] text-white'
                  : 'bg-[var(--bg-secondary)] border border-[var(--border)] text-[var(--text-primary)]'
              }`}
            >
              {msg.content}
            </div>
          </div>
        ))}

        {activeTask && activeTask.status === 'plan_ready' && (
          <PlanConfirmation task={activeTask} onConfirm={confirmTask} onCancel={cancelTask} />
        )}

        {activeTask && activeTask.status === 'executing' && (
          <PipelineLog repo={activeTask.repo} issueNumber={0} />
        )}

        {taskDiff && <DiffViewer diff={taskDiff} />}

        {taskDiff && (
          <div className="flex gap-3">
            <button className="px-4 py-2 rounded-lg bg-[var(--success)] text-white text-sm font-medium hover:bg-[var(--success)]/80 transition-colors">
              Commit
            </button>
            <button
              onClick={() => setTaskDiff('')}
              className="px-4 py-2 rounded-lg bg-[var(--danger)]/20 text-[var(--danger)] text-sm hover:bg-[var(--danger)]/30 transition-colors"
            >
              Discard
            </button>
          </div>
        )}

        {sending && (
          <div className="flex justify-start">
            <div className="bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-4 py-3 text-sm text-[var(--text-secondary)]">
              {mode === 'task' ? 'Creating execution plan...' : 'Thinking...'}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="px-6 py-4 border-t border-[var(--border)]">
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && send()}
            placeholder={mode === 'task' ? 'Describe a coding task...' : 'Ask a question...'}
            className="flex-1 bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] focus:outline-none focus:border-[var(--accent)]"
          />
          <button
            onClick={send}
            disabled={sending || !input.trim()}
            className="px-5 py-2.5 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--accent-hover)] disabled:opacity-40 transition-colors"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
