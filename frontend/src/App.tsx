import { useEffect } from "react";
import { createBrowserRouter, RouterProvider } from "react-router";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import { useAuth } from "./lib/auth";
import { startLive, stopLive } from "./lib/live";
import { AgentsPage } from "./pages/Agents";
import { LoginPage } from "./pages/Login";
import { MarketPage } from "./pages/Market";
import { OfficePage } from "./pages/Office";
import { SettingsPage } from "./pages/Settings";
import { StrategiesPage } from "./pages/Strategies";
import { TradesPage } from "./pages/Trades";

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <OfficePage /> },
      { path: "/agentes", element: <AgentsPage /> },
      { path: "/estrategias", element: <StrategiesPage /> },
      { path: "/mercado", element: <MarketPage /> },
      { path: "/operacoes", element: <TradesPage /> },
      { path: "/config", element: <SettingsPage /> },
      { path: "*", element: <OfficePage /> },
    ],
  },
]);

export function App() {
  const { loading, status } = useAuth();
  const logged = !!status?.logged_in;
  useEffect(() => {
    if (logged) startLive();
    else stopLive();
  }, [logged]);
  if (loading) return <Loading label="Abrindo o escritório…" />;
  if (!logged) return <LoginPage />;
  return <RouterProvider router={router} />;
}
