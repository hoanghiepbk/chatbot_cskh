import {
  BulbOutlined,
  CustomerServiceOutlined,
  DashboardOutlined,
  ExperimentOutlined,
  NodeIndexOutlined,
} from "@ant-design/icons";
import { Authenticated, Refine } from "@refinedev/core";
import { RefineKbar, RefineKbarProvider } from "@refinedev/kbar";

import {
  ErrorComponent,
  ThemedLayout,
  ThemedSider,
  useNotificationProvider,
} from "@refinedev/antd";
import "@refinedev/antd/dist/reset.css";

import routerProvider, {
  CatchAllNavigate,
  DocumentTitleHandler,
  NavigateToResource,
  UnsavedChangesNotifier,
} from "@refinedev/react-router";
import { App as AntdApp } from "antd";
import { useEffect } from "react";
import { BrowserRouter, Outlet, Route, Routes, useNavigate } from "react-router";
import { authProvider } from "./auth/authProvider";
import { Login } from "./auth/Login";
import { Header } from "./components/header";
import { ColorModeContextProvider } from "./contexts/color-mode";
import { ConversationList } from "./pages/conversations/list";
import { ConversationShow } from "./pages/conversations/show";
import { EvalDashboard } from "./pages/evals";
import { KnowledgeGapsPage } from "./pages/insights";
import { HitlQueue } from "./pages/queue";
import { OpsDashboard } from "./pages/ops";
import { dataProvider } from "./providers/data";
import { SEMANTIC } from "./theme";

// Bounce to /login when a request 401s mid-session (client.ts fires this event).
function UnauthorizedListener() {
  const navigate = useNavigate();
  useEffect(() => {
    const handler = () => navigate("/login");
    window.addEventListener("xecare:unauthorized", handler);
    return () => window.removeEventListener("xecare:unauthorized", handler);
  }, [navigate]);
  return null;
}

function ConsoleTitle({ collapsed }: { collapsed: boolean }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "0 16px",
        height: 64,
        fontWeight: 700,
        fontSize: 15,
      }}
    >
      <span style={{ color: SEMANTIC.accent, fontSize: 18 }}>◆</span>
      {!collapsed && <span>XeCare Console</span>}
    </div>
  );
}

function App() {
  return (
    <BrowserRouter>
      <RefineKbarProvider>
        <ColorModeContextProvider>
          <AntdApp>
            <Refine
              authProvider={authProvider}
              notificationProvider={useNotificationProvider}
              routerProvider={routerProvider}
              dataProvider={dataProvider}
              resources={[
                {
                  name: "conversations",
                  list: "/conversations",
                  show: "/conversations/:id",
                  meta: { label: "Trace Explorer", icon: <NodeIndexOutlined /> },
                },
                {
                  name: "ops",
                  list: "/ops",
                  meta: { label: "Ops Dashboard", icon: <DashboardOutlined /> },
                },
                {
                  name: "evals",
                  list: "/evals",
                  meta: { label: "Eval Dashboard", icon: <ExperimentOutlined /> },
                },
                {
                  name: "queue",
                  list: "/queue",
                  meta: { label: "HITL Queue", icon: <CustomerServiceOutlined /> },
                },
                {
                  name: "insights",
                  list: "/insights",
                  meta: { label: "Knowledge Gaps", icon: <BulbOutlined /> },
                },
              ]}
              options={{
                syncWithLocation: true,
                warnWhenUnsavedChanges: false,
                projectId: "y9GaA3-MrFMPn-utBns4",
              }}
            >
              <UnauthorizedListener />
              <Routes>
                <Route
                  element={
                    <Authenticated key="auth-in" fallback={<CatchAllNavigate to="/login" />}>
                      <ThemedLayout
                        Header={() => <Header sticky />}
                        Sider={(props) => <ThemedSider {...props} fixed />}
                        Title={ConsoleTitle}
                      >
                        <Outlet />
                      </ThemedLayout>
                    </Authenticated>
                  }
                >
                  <Route index element={<NavigateToResource resource="conversations" />} />
                  <Route path="/conversations">
                    <Route index element={<ConversationList />} />
                    <Route path=":id" element={<ConversationShow />} />
                  </Route>
                  <Route path="/ops" element={<OpsDashboard />} />
                  <Route path="/evals" element={<EvalDashboard />} />
                  <Route path="/queue" element={<HitlQueue />} />
                  <Route path="/insights" element={<KnowledgeGapsPage />} />
                  <Route path="*" element={<ErrorComponent />} />
                </Route>

                <Route
                  element={
                    <Authenticated key="auth-out" fallback={<Outlet />}>
                      <NavigateToResource resource="conversations" />
                    </Authenticated>
                  }
                >
                  <Route path="/login" element={<Login />} />
                </Route>
              </Routes>

              <RefineKbar />
              <UnsavedChangesNotifier />
              <DocumentTitleHandler />
            </Refine>
          </AntdApp>
        </ColorModeContextProvider>
      </RefineKbarProvider>
    </BrowserRouter>
  );
}

export default App;
