import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Layout } from "./components/Layout";
import "./index.css";
import { AvatarStudioScreen } from "./screens/AvatarStudioScreen";
import { ChatScreen } from "./screens/ChatScreen";
import { CodingScreen } from "./screens/CodingScreen";
import { DevicesScreen } from "./screens/DevicesScreen";
import { GoalsScreen } from "./screens/GoalsScreen";
import { MemoryScreen } from "./screens/MemoryScreen";
import { SettingsScreen } from "./screens/SettingsScreen";
import { StoreProvider, useStore } from "./store";

function App() {
  const { screen, state, go } = useStore();
  const [drawer, setDrawer] = useState(false);
  useEffect(() => setDrawer(false), [screen]);
  // first run: no model yet → settings
  useEffect(() => {
    if (state && !state.config.ready && !location.hash) go("settings");
  }, [state, go]);
  if (!state) return <div className="flex h-dvh items-center justify-center text-muted">Connecting to Nemo…</div>;
  const body = {
    chat: <ChatScreen openDrawer={() => setDrawer(true)} />,
    goals: <GoalsScreen />,
    memory: <MemoryScreen />,
    devices: <DevicesScreen />,
    coding: <CodingScreen />,
    avatar: <AvatarStudioScreen />,
    settings: <SettingsScreen />,
  }[screen];
  return (
    <Layout drawer={drawer} setDrawer={setDrawer}>
      {body}
    </Layout>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <StoreProvider>
      <App />
    </StoreProvider>
  </StrictMode>,
);
