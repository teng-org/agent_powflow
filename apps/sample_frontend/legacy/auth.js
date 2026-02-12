// 优先使用环境变量注入的 VITE_API_BASE，或同源 /api，避免写死本地回环
const API_BASE =
  (typeof VITE_API_BASE !== "undefined" && VITE_API_BASE) ||
  `${window.location.origin}/api`;

const usernameInput = document.getElementById("usernameInput");
const passwordInput = document.getElementById("passwordInput");
const loginBtn = document.getElementById("loginBtn");
const registerBtn = document.getElementById("registerBtn");
const statusText = document.getElementById("statusText");
const continueBtn = document.getElementById("continueBtn");

let authToken = localStorage.getItem("authToken") || "";

function setStatus(text) {
  statusText.textContent = text;
}

async function authFetch(url, options = {}) {
  const headers = options.headers || {};
  if (authToken) {
    headers.Authorization = `Bearer ${authToken}`;
  }
  return fetch(url, { ...options, headers });
}

async function checkSession() {
  if (!authToken) return;
  const res = await authFetch(`${API_BASE}/auth/me`);
  if (!res.ok) {
    authToken = "";
    localStorage.removeItem("authToken");
    return;
  }
  const data = await res.json();
  setStatus(`Signed in as ${data.username}`);
  continueBtn.hidden = false;
}

async function handleLogin() {
  const username = usernameInput.value.trim();
  const password = passwordInput.value.trim();
  if (!username || !password) {
    setStatus("Username and password required");
    return;
  }
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    setStatus("Login failed");
    return;
  }
  const data = await res.json();
  authToken = data.access_token;
  localStorage.setItem("authToken", authToken);
  setStatus("Login success");
  continueBtn.hidden = false;
  setTimeout(() => {
    window.location.href = "chat.html";
  }, 300);
}

async function handleRegister() {
  const username = usernameInput.value.trim();
  const password = passwordInput.value.trim();
  if (!username || !password) {
    setStatus("Username and password required");
    return;
  }
  const res = await fetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    setStatus("Register failed");
    return;
  }
  await handleLogin();
}

loginBtn.addEventListener("click", handleLogin);
registerBtn.addEventListener("click", handleRegister);
continueBtn.addEventListener("click", () => {
  window.location.href = "chat.html";
});
passwordInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    handleLogin();
  }
});

checkSession();
