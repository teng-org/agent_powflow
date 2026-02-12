import { reactive } from "vue";
import { API_BASE } from "./api";

export const authState = reactive({
  token: localStorage.getItem("authToken") || "",
  user: null,
  checked: false,
});

// 后台（/api/admin/...）单独的登录态
export const adminAuthState = reactive({
  token: localStorage.getItem("adminToken") || "",
  user: null,
  checked: false,
});

export const kbAuthState = reactive({
  token: localStorage.getItem("kbToken") || "",
  checked: false,
});

export const delAuthState = reactive({
  token: localStorage.getItem("delToken") || "",
  expiresAt: Number(localStorage.getItem("delTokenExp") || 0),
});

function persistToken(token) {
  authState.token = token || "";
  if (authState.token) {
    localStorage.setItem("authToken", authState.token);
  } else {
    localStorage.removeItem("authToken");
  }
}

function persistAdminToken(token) {
  adminAuthState.token = token || "";
  if (adminAuthState.token) {
    localStorage.setItem("adminToken", adminAuthState.token);
  } else {
    localStorage.removeItem("adminToken");
  }
}

function persistKbToken(token) {
  kbAuthState.token = token || "";
  if (kbAuthState.token) {
    localStorage.setItem("kbToken", kbAuthState.token);
  } else {
    localStorage.removeItem("kbToken");
  }
}

function persistDelToken(token, expiresAt = 0) {
  delAuthState.token = token || "";
  delAuthState.expiresAt = expiresAt || 0;
  if (delAuthState.token) {
    localStorage.setItem("delToken", delAuthState.token);
    localStorage.setItem("delTokenExp", String(delAuthState.expiresAt));
  } else {
    localStorage.removeItem("delToken");
    localStorage.removeItem("delTokenExp");
  }
}

function isDelTokenValid() {
  return delAuthState.token && delAuthState.expiresAt > Date.now();
}

export function authFetch(url, options = {}) {
  const headers = options.headers ? { ...options.headers } : {};
  if (authState.token) {
    headers.Authorization = `Bearer ${authState.token}`;
  }
  if (kbAuthState.token) {
    headers["X-KB-Token"] = kbAuthState.token;
  }
  if (isDelTokenValid()) {
    headers["X-DEL-Token"] = delAuthState.token;
  } else if (delAuthState.token) {
    persistDelToken("", 0);
  }
  return fetch(url, { ...options, headers });
}

// 后台接口专用 fetch，带 admin token，不混用前台 token
export function adminFetch(url, options = {}) {
  const headers = options.headers ? { ...options.headers } : {};
  if (adminAuthState.token) {
    headers.Authorization = `Bearer ${adminAuthState.token}`;
  }
  return fetch(url, { ...options, headers });
}

export async function fetchMe() {
  if (!authState.token) {
    authState.user = null;
    authState.checked = true;
    return null;
  }
  const res = await authFetch(`${API_BASE}/auth/me`);
  if (!res.ok) {
    persistToken("");
    authState.user = null;
    authState.checked = true;
    return null;
  }
  const data = await res.json();
  authState.user = data;
  authState.checked = true;
  return data;
}

export async function fetchAdminMe() {
  if (!adminAuthState.token) {
    adminAuthState.user = null;
    adminAuthState.checked = true;
    return null;
  }
  const res = await adminFetch(`${API_BASE}/admin/auth/me`);
  if (!res.ok) {
    persistAdminToken("");
    adminAuthState.user = null;
    adminAuthState.checked = true;
    return null;
  }
  const data = await res.json();
  adminAuthState.user = data;
  adminAuthState.checked = true;
  return data;
}

export async function fetchKbMe() {
  if (!kbAuthState.token) {
    kbAuthState.checked = true;
    return null;
  }
  const res = await authFetch(`${API_BASE}/auth/knowledge/me`);
  if (!res.ok) {
    persistKbToken("");
    kbAuthState.checked = true;
    return null;
  }
  kbAuthState.checked = true;
  return await res.json();
}

export async function login(username, password) {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    return { ok: false };
  }
  const data = await res.json();
  persistToken(data.access_token);
  persistKbToken("");
  persistDelToken("", 0);
  await fetchMe();
  return { ok: true };
}

// 后台登录，只获得 admin token
export async function adminLogin(username, password) {
  const res = await fetch(`${API_BASE}/admin/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    let detail = "";
    try {
      const d = await res.json();
      detail = d.detail || "";
    } catch (_) {}
    return { ok: false, detail };
  }
  const data = await res.json();
  persistAdminToken(data.access_token);
  adminAuthState.checked = false;
  await fetchAdminMe();
  return { ok: true };
}

export async function register(username, password) {
  const res = await fetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    return { ok: false };
  }
  await login(username, password);
  return { ok: true };
}

export function logout() {
  persistToken("");
  persistKbToken("");
  persistDelToken("", 0);
  authState.user = null;
  authState.checked = true;
  kbAuthState.checked = false;
}

export function adminLogout() {
  persistAdminToken("");
  adminAuthState.user = null;
  adminAuthState.checked = false;
}

export async function kbLogin(password) {
  const res = await authFetch(`${API_BASE}/auth/knowledge/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!res.ok) {
    return { ok: false };
  }
  const data = await res.json();
  persistKbToken(data.access_token);
  kbAuthState.checked = true;
  return { ok: true };
}

export async function changeDeletePassword(current_password, new_password) {
  const res = await authFetch(`${API_BASE}/auth/delete-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password, new_password }),
  });
  if (!res.ok) return { ok: false };
  const data = await res.json();
  authState.user = data;
  return { ok: true, data };
}

export async function deleteLogin(password) {
  const res = await authFetch(`${API_BASE}/auth/delete-login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!res.ok) return { ok: false };
  const data = await res.json();
  // token有效1分钟
  persistDelToken(data.access_token, Date.now() + 60 * 1000);
  return { ok: true };
}

export function clearDeleteToken() {
  persistDelToken("", 0);
}

export function deleteTokenValid() {
  return isDelTokenValid();
}

export function kbLogout() {
  persistKbToken("");
  kbAuthState.checked = false;
}

export async function updateProfile(payload) {
  const res = await authFetch(`${API_BASE}/auth/profile`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) return { ok: false };
  const data = await res.json();
  authState.user = data;
  return { ok: true, data };
}

export async function changePassword(current_password, new_password) {
  const res = await authFetch(`${API_BASE}/auth/password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password, new_password }),
  });
  if (!res.ok) {
    let detail = "unknown";
    try {
      const d = await res.json();
      detail = d.detail || detail;
    } catch (_) {}
    return { ok: false, detail };
  }
  const data = await res.json();
  authState.user = data;
  return { ok: true, data };
}

export async function changeKnowledgePassword(current_password, new_password) {
  const res = await authFetch(`${API_BASE}/auth/knowledge-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password, new_password }),
  });
  if (!res.ok) {
    let detail = "unknown";
    try {
      const d = await res.json();
      detail = d.detail || detail;
    } catch (_) {}
    return { ok: false, detail };
  }
  const data = await res.json();
  authState.user = data;
  return { ok: true, data };
}
