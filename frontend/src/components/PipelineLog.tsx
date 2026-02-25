import { useEffect, useRef, useState } from 'react';
import { createSSEStream } from '../api/client';

type LogEntry = {
  timestamp?: string;
  phase?: string;
  status?: string;
  message?: string;
  data?: unknown;
};

export function PipelineLog({ repo, issueNumber }: { repo: string; issueNumber: number }) {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [connected, setConnected] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const path = issueNumber > 0
      ? `/pipeline/${repo}/${issueNumber}/stream`
      : `/pipeline/${repo}/stream`;
    let es: EventSource | null = null;
    try {
      es = createSSEStream(
        path,
        (data) => {
          setLogs((prev) => [...prev.slice(-500), data as LogEntry]);
          setConnected(true);
        },
        () => setConnected(false),
      );
    } catch {
      setConnected(false);
    }
    return () => es?.close();
  }, [repo, issueNumber]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  return (
    <div className="rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)]">
      <div className="flex items-center justify-between px-4 py-2 border-b border-[var(--border)]">
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${connected ? 'bg-[var(--success)] animate-pulse' : 'bg-[var(--text-secondary)]'}`} />
          <span className="text-xs text-[var(--text-secondary)]">Pipeline Output</span>
        </div>
        <button
          onClick={() => setLogs([])}
          className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
        >
          Clear
        </button>
      </div>
      <div className="max-h-[400px] overflow-auto p-3 font-mono text-xs space-y-0.5">
        {logs.length === 0 ? (
          <p className="text-[var(--text-secondary)]">Waiting for pipeline events...</p>
        ) : (
          logs.map((entry, i) => {
            const statusColor =
              entry.status === 'ok' || entry.status === 'passed'
                ? 'text-[var(--success)]'
                : entry.status === 'failed'
                ? 'text-[var(--danger)]'
                : 'text-[var(--text-secondary)]';
            return (
              <div key={i} className="flex gap-2">
                <span className="text-[var(--text-secondary)] w-14 text-right flex-shrink-0">
                  {entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString() : ''}
                </span>
                <span className="text-[var(--accent)] w-20 flex-shrink-0">{entry.phase || ''}</span>
                <span className={`w-12 flex-shrink-0 ${statusColor}`}>{entry.status || ''}</span>
                <span className="text-[var(--text-primary)] break-all">{entry.message || JSON.stringify(entry.data || '')}</span>
              </div>
            );
          })
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
