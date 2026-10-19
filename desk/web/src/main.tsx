import "./theme.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, NavLink, Route, Routes } from "react-router-dom";

import { type Meta, useApi } from "./api";
import { Loading } from "./components/ui";
import { SectionsContext } from "./sections";
import Agent from "./pages/Agent";
import Compare from "./pages/Compare";
import Downloads from "./pages/Downloads";
import Holdings from "./pages/Holdings";
import Notebook from "./pages/Notebook";
import Ops from "./pages/Ops";
import Overview from "./pages/Overview";
import Replay from "./pages/Replay";
import Research from "./pages/Research";
import Symbol from "./pages/Symbol";
import Trades from "./pages/Trades";
import Versions from "./pages/Versions";

// Pages appear only when the owner made one of their sections public (GET /api/meta).
const PAGES = [
  { path: "/holdings", label: "Holdings", sections: ["positions"], element: () => <Holdings /> },
  { path: "/compare", label: "Compare", sections: ["compare"], element: (s: string[]) => <Compare sections={s} /> },
  { path: "/trades", label: "Trades", sections: ["trades"], element: () => <Trades /> },
  { path: "/symbol", label: "Symbols", sections: ["symbols"], element: () => <Symbol /> },
  { path: "/replay", label: "Replay", sections: ["replay"], element: () => <Replay /> },
  { path: "/versions", label: "What changed", sections: ["versions"], element: () => <Versions /> },
  { path: "/research", label: "Research", sections: ["research"], element: () => <Research /> },
  { path: "/notebook", label: "Notebook", sections: ["journal", "memory"], element: (s: string[]) => <Notebook sections={s} /> },
  { path: "/agent", label: "Agent activity", sections: ["agent"], element: () => <Agent /> },
  { path: "/ops", label: "Operations", sections: ["ops"], element: () => <Ops /> },
  { path: "/downloads", label: "Downloads", sections: ["downloads"], element: () => <Downloads /> },
];

function App() {
  const meta = useApi<Meta>("/api/meta");
  if (!meta.data) return <div className="shell"><Loading error={meta.error} /></div>;
  const sections = meta.data.sections;
  const pages = PAGES.filter((p) => p.sections.some((s) => sections.includes(s)));
  return (
    <SectionsContext.Provider value={sections}>
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
        {pages.map((p) => <Route key={p.path} path={p.path} element={p.element(sections)} />)}
        {sections.includes("symbols") && <Route path="/symbol/:sym" element={<Symbol />} />}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <footer>Read-only view of two paper-trading bots. Nothing here is investment advice.</footer>
    </div>
    </SectionsContext.Provider>
  );
}

const client = new QueryClient();
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}><BrowserRouter><App /></BrowserRouter></QueryClientProvider>
  </StrictMode>,
);
