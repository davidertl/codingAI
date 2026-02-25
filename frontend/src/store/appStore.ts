import { create } from 'zustand';
import type { LiveSnapshot, Repo, Project, HealthResponse, ChatMessage, RoutingEntry } from '../api/client';

type AppState = {
  connected: boolean;
  selectedRepo: string | null;
  health: HealthResponse | null;
  repos: Repo[];
  projects: Project[];
  workers: Record<string, { repo: string; running: boolean }>;
  summary: Record<string, unknown> | null;
  rules: Record<string, unknown> | null;
  sidebarOpen: boolean;
  chatMessages: ChatMessage[];
  routing: Record<string, RoutingEntry>;

  setConnected: (v: boolean) => void;
  setSelectedRepo: (repo: string | null) => void;
  applySnapshot: (snap: LiveSnapshot) => void;
  toggleSidebar: () => void;
  addChatMessage: (msg: ChatMessage) => void;
  clearChat: () => void;
  setRouting: (r: Record<string, RoutingEntry>) => void;
};

export const useAppStore = create<AppState>((set) => ({
  connected: false,
  selectedRepo: null,
  health: null,
  repos: [],
  projects: [],
  workers: {},
  summary: null,
  rules: null,
  sidebarOpen: true,
  chatMessages: [],
  routing: {},

  setConnected: (v) => set({ connected: v }),
  setSelectedRepo: (repo) => set({ selectedRepo: repo }),

  applySnapshot: (snap) =>
    set({
      connected: true,
      health: snap.health,
      repos: snap.repos || [],
      projects: snap.projects || [],
      workers: snap.workers || {},
      summary: snap.summary || null,
      rules: snap.rules || null,
      selectedRepo: snap.selected_repo || null,
    }),

  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),

  addChatMessage: (msg) =>
    set((s) => ({ chatMessages: [...s.chatMessages, msg] })),

  clearChat: () => set({ chatMessages: [] }),

  setRouting: (r) => set({ routing: r }),
}));
