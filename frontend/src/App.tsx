import { useEffect } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Dashboard } from './pages/Dashboard';
import { Issues } from './pages/Issues';
import { IssueDetail } from './pages/IssueDetail';
import { Chat } from './pages/Chat';
import { Settings } from './pages/Settings';
import { Rules } from './pages/Rules';
import { Models } from './pages/Models';
import { createLiveSocket } from './api/client';
import { useAppStore } from './store/appStore';

export default function App() {
  const applySnapshot = useAppStore((s) => s.applySnapshot);
  const setConnected = useAppStore((s) => s.setConnected);

  useEffect(() => {
    const ws = createLiveSocket(
      (snap) => applySnapshot(snap),
      () => setConnected(false),
    );
    return () => ws.close();
  }, [applySnapshot, setConnected]);

  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="issues" element={<Issues />} />
          <Route path="issues/:repo/:number" element={<IssueDetail />} />
          <Route path="chat" element={<Chat />} />
          <Route path="settings" element={<Settings />} />
          <Route path="rules" element={<Rules />} />
          <Route path="models" element={<Models />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
