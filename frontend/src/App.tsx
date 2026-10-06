import { lazy, Suspense, useEffect, type ReactNode } from "react";
import { createBrowserRouter, RouterProvider } from "react-router";
import { Layout } from "./components/Layout";
import { Loading, Toasts } from "./components/ui";
import { useAuth } from "./lib/auth";
import { startLive, stopLive } from "./lib/live";
import { LoginPage } from "./pages/Login";
import { OfficePage } from "./pages/Office";

// telas abertas sob demanda: o escritório carrega primeiro e mais rápido no celular
const AgentsPage = lazy(() => import("./pages/Agents").then((m) => ({ default: m.AgentsPage })));
const DailyPage = lazy(() => import("./pages/Daily").then((m) => ({ default: m.DailyPage })));
const StrategiesPage = lazy(() => import("./pages/Strategies").then((m) => ({ default: m.StrategiesPage })));
const TradesPage = lazy(() => import("./pages/Trades").then((m) => ({ default: m.TradesPage })));
const SettingsPage = lazy(() => import("./pages/Settings").then((m) => ({ default: m.SettingsPage })));

const page = (el: ReactNode) => <Suspense fallback={<Loading />}>{el}</Suspense>;

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <OfficePage /> },
      { path: "/agentes", element: page(<AgentsPage />) },
      { path: "/daily", element: page(<DailyPage />) },
      { path: "/estrategias", element: page(<StrategiesPage />) },
      { path: "/operacoes", element: page(<TradesPage />) },
      { path: "/config", element: page(<SettingsPage />) },
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
  return (
    <>
      {logged ? <RouterProvider router={router} /> : <LoginPage />}
      <Toasts />
    </>
  );
}
