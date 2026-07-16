import { Routes, Route, Navigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useSession } from "./context/SessionContext";
import NavBar from "./components/NavBar";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import InvoiceDetail from "./pages/InvoiceDetail";
import Chat from "./pages/Chat";
import Documents from "./pages/Documents";

function RequireSession({ children }: { children: ReactNode }) {
  const { token } = useSession();
  if (!token) return <Navigate to="/login" replace />;
  return (
    <div className="min-h-screen bg-[#fafafa]">
      <NavBar />
      {children}
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
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
    </Routes>
  );
}
