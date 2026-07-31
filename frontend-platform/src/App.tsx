import { BrowserRouter as Router, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import AdminsPage from "./pages/Admins";
import DashboardPage from "./pages/Dashboard";
import KnowledgeGovernancePage from "./pages/KnowledgeGovernance";
import LoginPage from "./pages/Login";
import SettingsPage from "./pages/Settings";
import TenantsPage from "./pages/Tenants";
import WorkspaceDbWhitelistPage from "./pages/WorkspaceDbWhitelist";

const appBase = (import.meta.env.BASE_URL || "/").replace(/\/$/, "");
const routerBasename = appBase && appBase !== "/" ? appBase : undefined;
const dashboardHref = routerBasename ? `${routerBasename}/dashboard` : "/dashboard";

const ProtectedRoute = ({ children }: { children: React.ReactNode }) => {
  const token = localStorage.getItem("platform_token");
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  return <Layout>{children}</Layout>;
};

function App() {
  return (
    <Router basename={routerBasename}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />

        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <DashboardPage />
            </ProtectedRoute>
          }
        />

        <Route
          path="/tenants"
          element={
            <ProtectedRoute>
              <TenantsPage />
            </ProtectedRoute>
          }
        />

        <Route
          path="/knowledge-governance"
          element={
            <ProtectedRoute>
              <KnowledgeGovernancePage />
            </ProtectedRoute>
          }
        />

        <Route
          path="/admins"
          element={
            <ProtectedRoute>
              <AdminsPage />
            </ProtectedRoute>
          }
        />

        <Route
          path="/settings"
          element={
            <ProtectedRoute>
              <SettingsPage />
            </ProtectedRoute>
          }
        />

        <Route
          path="/db-whitelist"
          element={
            <ProtectedRoute>
              <WorkspaceDbWhitelistPage />
            </ProtectedRoute>
          }
        />

        <Route path="/workspace-db-whitelist" element={<Navigate to="/db-whitelist" replace />} />
        <Route path="/" element={<Navigate to="/dashboard" replace />} />

        <Route
          path="*"
          element={
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                minHeight: "100vh",
                flexDirection: "column",
                gap: 16,
              }}
            >
              <h1 style={{ fontSize: 48, fontWeight: "bold", color: "#d1d5db" }}>404</h1>
              <p style={{ color: "#6b7280" }}>Page not found</p>
              <a href={dashboardHref} style={{ color: "#3b82f6" }}>
                Back to dashboard
              </a>
            </div>
          }
        />
      </Routes>
    </Router>
  );
}

export default App;
