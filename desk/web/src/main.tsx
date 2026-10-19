import "./theme.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, NavLink, Route, Routes } from "react-router-dom";

import { type Meta, useApi } from "./api";
import { Loading } from "./components/ui";
import Compare from "./pages/Compare";
import Ops from "./pages/Ops";
import Overview from "./pages/Overview";
import Trades from "./pages/Trades";
import Versions from "./pages/Versions";

// Pages appear only when the owner made their section public (GET /api/meta).
const PAGES = [
  { path: "/compare", label: "Compare", section: "compare", element: <Compare /> },
  { path: "/trades", label: "Trades", section: "trades", element: <Trades /> },
  { path: "/versions", label: "What changed", section: "versions", element: <Versions /> },
  { path: "/ops", label: "Operations", section: "ops", element: <Ops /> },
];

function App() {
  const meta = useApi<Meta>("/api/meta");
  if (!meta.data) return <div className="shell"><Loading error={meta.error} /></div>;
  const sections = meta.data.sections;
  const pages = PAGES.filter((p) => sections.includes(p.section));
  return (
    <div className="shell">
      <header className="top">
        <NavLink to="/" className="brand">claude-desk</NavLink>
        <nav>
          <NavLink to="/" end>Overview</NavLink>
          {pages.map((p) => <NavLink key={p.path} to={p.path}>{p.label}</NavLink>)}
        </nav>
      </header>
      <Routes>
        <Route path="/" element={<Overview sections={sections} />} />
        {pages.map((p) => <Route key={p.path} path={p.path} element={p.element} />)}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <footer>Read-only view of two paper-trading bots. Nothing here is investment advice.</footer>
    </div>
  );
}

const client = new QueryClient();
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}><BrowserRouter><App /></BrowserRouter></QueryClientProvider>
  </StrictMode>,
);
