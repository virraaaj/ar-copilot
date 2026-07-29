import { Routes, Route, Navigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useSession } from "./context/SessionContext";
import NavBar from "./components/NavBar";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import InvoiceDetail from "./pages/InvoiceDetail";
import Chat from "./pages/Chat";
import Documents from "./pages/Documents";
import MagicLink from "./pages/MagicLink";
import ProjectInvoicePicker from "./pages/ProjectInvoicePicker";
import Settings from "./pages/Settings";
import Chases from "./pages/Chases";
import Outbox from "./pages/Outbox";
import AgentDashboard from "./pages/agent/AgentDashboard";
import AgentCockpit from "./pages/agent/AgentCockpit";
import AgentCaseList from "./pages/agent/AgentCaseList";
import SeedScenarioRunner from "./pages/agent/SeedScenarioRunner";

function RequireSession({ children }: { children: ReactNode }) {
  const { token } = useSession();
  if (!token) return <Navigate to="/login" replace />;
  return (
    <div className="min-h-screen bg-[#fafafa] dark:bg-zinc-950">
      <NavBar />
      {children}
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/link" element={<MagicLink />} />
      <Route
        path="/projects/:projectNumber/pick-invoice"
        element={
          <RequireSession>
            <ProjectInvoicePicker />
          </RequireSession>
        }
      />
      <Route
        path="/"
        element={
          <RequireSession>
            <Dashboard />
          </RequireSession>
        }
      />
      <Route
        path="/invoices/:invoiceId"
        element={
          <RequireSession>
            <InvoiceDetail />
          </RequireSession>
        }
      />
      <Route
        path="/chat"
        element={
          <RequireSession>
            <Chat />
          </RequireSession>
        }
      />
      <Route
        path="/documents"
        element={
          <RequireSession>
            <Documents />
          </RequireSession>
        }
      />
      <Route
        path="/chases"
        element={
          <RequireSession>
            <Chases />
          </RequireSession>
        }
      />
      <Route
        path="/agent"
        element={
          <RequireSession>
            <AgentDashboard />
          </RequireSession>
        }
      />
      <Route
        path="/agent/cases"
        element={
          <RequireSession>
            <AgentCaseList />
          </RequireSession>
        }
      />
      <Route
        path="/agent/cases/:caseId"
        element={
          <RequireSession>
            <AgentCockpit />
          </RequireSession>
        }
      />
      <Route
        path="/agent/scenarios"
        element={
          <RequireSession>
            <SeedScenarioRunner />
          </RequireSession>
        }
      />
      <Route
        path="/outbox"
        element={
          <RequireSession>
            <Outbox />
          </RequireSession>
        }
      />
      <Route
        path="/settings"
        element={
          <RequireSession>
            <Settings />
          </RequireSession>
        }
      />
    </Routes>
  );
}
