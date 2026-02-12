import { createRouter, createWebHistory } from "vue-router";
import Home from "./pages/Home.vue";
import Chat from "./pages/Chat.vue";
import Admin from "./pages/Admin.vue";
import DocumentChunks from "./pages/DocumentChunks.vue";
import Account from "./pages/Account.vue";
import System from "./pages/System.vue";
import AdminLogin from "./pages/AdminLogin.vue";
import {
  authState,
  kbAuthState,
  adminAuthState,
  fetchMe,
  fetchKbMe,
  fetchAdminMe,
} from "./lib/auth";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: Home },
    {
      path: "/chat",
      name: "chat",
      component: Chat,
      meta: { requiresAuth: true },
    },
    {
      path: "/knowledge",
      name: "knowledge",
      component: Admin,
      meta: { requiresAdmin: true },
    },
    {
      path: "/knowledge/doc/:id",
      name: "knowledge-doc",
      component: DocumentChunks,
      meta: { requiresAdmin: true },
    },
    {
      path: "/account",
      name: "account",
      component: Account,
      meta: { requiresAuth: true },
    },
    {
      path: "/system",
      name: "system",
      component: System,
      meta: { requiresAdmin: true },
    },
    {
      path: "/system/admin-login",
      name: "admin-login",
      component: AdminLogin,
    },
    { path: "/admin", redirect: "/knowledge" },
  ],
});

router.beforeEach(async (to) => {
  // 后台路由独立守卫
  if (to.meta.requiresAdmin) {
    if (!adminAuthState.token) {
      return {
        name: "admin-login",
        query: { redirect: to.fullPath },
      };
    }
    if (!adminAuthState.checked) {
      await fetchAdminMe();
    }
    if (!adminAuthState.user) {
      return { name: "admin-login", query: { redirect: to.fullPath } };
    }
    return true;
  }

  if (!to.meta.requiresAuth) return true;
  if (!authState.token) return { name: "home" };
  if (!authState.checked) {
    await fetchMe();
  }
  if (!authState.user) return { name: "home" };
  if (to.meta.requiresKnowledge) {
    if (!kbAuthState.token) {
      return { name: "knowledge-login", query: { redirect: to.fullPath } };
    }
    if (!kbAuthState.checked) {
      await fetchKbMe();
    }
    if (!kbAuthState.token) {
      return { name: "knowledge-login", query: { redirect: to.fullPath } };
    }
  }
  return true;
});

export default router;
