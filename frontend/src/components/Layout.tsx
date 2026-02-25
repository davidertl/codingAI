import { NavLink, Outlet } from 'react-router-dom';
import { useAppStore } from '../store/appStore';

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', icon: '⊞' },
  { to: '/issues', label: 'Issues', icon: '◉' },
  { to: '/chat', label: 'Chat', icon: '◈' },
  { to: '/settings', label: 'Settings', icon: '⚙' },
  { to: '/rules', label: 'Rules', icon: '≡' },
  { to: '/models', label: 'Models', icon: '◆' },
];

export function Layout() {
  const { sidebarOpen, toggleSidebar, health, connected } = useAppStore();

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--bg-primary)]">
      <aside
        className={`${
          sidebarOpen ? 'w-56' : 'w-14'
        } flex flex-col border-r border-[var(--border)] bg-[var(--bg-secondary)] transition-all duration-200`}
      >
        <div className="flex items-center gap-2 px-3 py-4 border-b border-[var(--border)]">
          <button
            onClick={toggleSidebar}
            className="text-lg text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
          >
            {sidebarOpen ? '◁' : '▷'}
          </button>
          {sidebarOpen && (
            <span className="font-semibold text-[var(--text-primary)] truncate">
              CodingAI
            </span>
          )}
        </div>

        <nav className="flex-1 py-2 space-y-0.5">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 mx-1 rounded-md text-sm transition-colors ${
                  isActive
                    ? 'bg-[var(--accent)]/15 text-[var(--accent)]'
                    : 'text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)]'
                }`
              }
            >
              <span className="text-base w-5 text-center">{item.icon}</span>
              {sidebarOpen && <span className="truncate">{item.label}</span>}
            </NavLink>
          ))}
        </nav>

        <div className="px-3 py-3 border-t border-[var(--border)]">
          <div className="flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full ${
                connected ? 'bg-[var(--success)]' : 'bg-[var(--danger)]'
              }`}
            />
            {sidebarOpen && (
              <span className="text-xs text-[var(--text-secondary)] truncate">
                {connected
                  ? health?.status || 'Connected'
                  : 'Disconnected'}
              </span>
            )}
          </div>
        </div>
      </aside>

      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}
