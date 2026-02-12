const API_BASE =
  (typeof VITE_API_BASE !== "undefined" && VITE_API_BASE) ||
  `${window.location.origin}/api`;

const askBtn = document.getElementById("askBtn");
const questionInput = document.getElementById("question");
const messagesEl = document.getElementById("messages");
const newConvBtn = document.getElementById("newConvBtn");
const summaryBox = document.getElementById("summaryBox");
const convMeta = document.getElementById("convMeta");
const conversationTitle = document.getElementById("conversationTitle");
const logoutBtn = document.getElementById("logoutBtn");
const loginLink = document.getElementById("loginLink");
const userAvatar = document.getElementById("userAvatar");
const userName = document.getElementById("userName");
const userStatus = document.getElementById("userStatus");
const historyList = document.getElementById("historyList");

let conversationId = null;
let activeConversationId = null;
const DEBUG_STREAM = false;
let authToken = localStorage.getItem("authToken") || "";

function setAuthState(user) {
  if (user) {
    userName.textContent = user.username;
    userStatus.textContent = "Signed in";
    userAvatar.textContent = user.username.slice(0, 2).toUpperCase();
    logoutBtn.hidden = false;
    loginLink.hidden = true;
  } else {
    userName.textContent = "Guest";
    userStatus.textContent = "Not signed in";
    userAvatar.textContent = "?";
    logoutBtn.hidden = true;
    loginLink.hidden = false;
  }
  askBtn.disabled = !user;
  questionInput.disabled = !user;
}

async function authFetch(url, options = {}) {
  const headers = options.headers || {};
  if (authToken) {
    headers.Authorization = `Bearer ${authToken}`;
  }
  return fetch(url, { ...options, headers });
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderMarkdown(value) {
  if (window.marked && typeof window.marked.parse === "function") {
    return window.marked.parse(value || "");
  }
  let html = escapeHtml(value);
  html = html.replace(/```([\s\S]*?)```/g, (_match, code) => {
    return `<pre><code>${code}</code></pre>`;
  });
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
  html = html.replace(/\n/g, "<br>");
  return html;
}

function copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).catch(() => {});
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    document.execCommand("copy");
  } catch (err) {
    // ignore
  }
  document.body.removeChild(textarea);
}

function addMessage(role, content, sources) {
  const wrapper = document.createElement("div");
  wrapper.className = `message ${role}`;
  const avatar = document.createElement("div");
  avatar.className = `avatar ${role}`;
  avatar.textContent = role === "user" ? "You" : "AI";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  const textEl = document.createElement("div");
  textEl.dataset.raw = content || "";
  textEl.innerHTML = role === "ai" ? renderMarkdown(textEl.dataset.raw) : escapeHtml(textEl.dataset.raw);
  bubble.appendChild(textEl);
  let copyBtn = null;
  if (role === "ai") {
    copyBtn = document.createElement("button");
    copyBtn.className = "copy-btn hidden";
    copyBtn.type = "button";
    copyBtn.textContent = "Copy";
    copyBtn.addEventListener("click", () => {
      copyToClipboard(textEl.dataset.raw || "");
      copyBtn.textContent = "Copied";
      copyBtn.classList.add("copied");
      setTimeout(() => {
        copyBtn.textContent = "Copy";
        copyBtn.classList.remove("copied");
      }, 1200);
    });
    bubble.appendChild(copyBtn);
  }
  if (sources && sources.length) {
    const sourceEl = document.createElement("div");
    sourceEl.className = "source";
    sourceEl.textContent = `Sources: ${sources.join(", ")}`;
    bubble.appendChild(sourceEl);
  }
  if (role === "user") {
    wrapper.appendChild(bubble);
    wrapper.appendChild(avatar);
  } else {
    wrapper.appendChild(avatar);
    wrapper.appendChild(bubble);
    if (copyBtn) {
      wrapper.appendChild(copyBtn);
    }
  }
  messagesEl.appendChild(wrapper);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return { bubble, textEl, copyBtn };
}

function appendText(target, chunk) {
  target.dataset.raw = `${target.dataset.raw || ""}${chunk}`;
  target.innerHTML = renderMarkdown(target.dataset.raw);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function createConversation() {
  const res = await authFetch(`${API_BASE}/conversations`, { method: "POST" });
  if (!res.ok) {
    if (res.status === 401) {
      handleLogout();
    }
    return;
  }
  const data = await res.json();
  conversationId = data.conversation_id;
  activeConversationId = conversationId;
  convMeta.textContent = `Conversation: ${conversationId}`;
  await loadConversation();
  await loadConversations();
}

async function loadConversation() {
  if (!conversationId) return;
  const res = await authFetch(`${API_BASE}/conversations/${conversationId}`);
  if (!res.ok) {
    if (res.status === 401) {
      handleLogout();
    }
    return;
  }
  const data = await res.json();
  summaryBox.textContent = data.summary ? `Summary: ${data.summary}` : "Summary: -";
  conversationTitle.textContent = data.title ? data.title : "Chat";
}

function renderHistoryItem(conv) {
  const item = document.createElement("div");
  item.className = "history-item";
  if (conv.conversation_id === activeConversationId) {
    item.classList.add("active");
  }
  const header = document.createElement("div");
  header.className = "history-header";
  const title = document.createElement("div");
  title.textContent = conv.title ? conv.title : `Conversation #${conv.conversation_id}`;
  const deleteBtn = document.createElement("button");
  deleteBtn.className = "history-delete";
  deleteBtn.type = "button";
  deleteBtn.textContent = "Delete";
  deleteBtn.addEventListener("click", async (event) => {
    event.stopPropagation();
    if (!confirm("Delete this conversation?")) return;
    await deleteConversation(conv.conversation_id);
  });
  header.appendChild(title);
  const renameBtn = document.createElement("button");
  renameBtn.className = "history-delete";
  renameBtn.type = "button";
  renameBtn.textContent = "Rename";
  renameBtn.addEventListener("click", async (event) => {
    event.stopPropagation();
    const nextTitle = prompt("Rename conversation", conv.title || "");
    if (nextTitle === null) return;
    await renameConversation(conv.conversation_id, nextTitle.trim());
  });
  header.appendChild(renameBtn);
  header.appendChild(deleteBtn);
  item.appendChild(header);
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = conv.summary ? conv.summary.slice(0, 60) : "No summary yet";
  item.appendChild(meta);
  item.addEventListener("click", async () => {
    activeConversationId = conv.conversation_id;
    conversationId = conv.conversation_id;
    convMeta.textContent = `Conversation: ${conversationId}`;
    await loadConversation();
    await loadMessages();
    await loadConversations();
  });
  return item;
}

async function loadConversations() {
  if (!authToken) return;
  const res = await authFetch(`${API_BASE}/conversations`);
  if (!res.ok) {
    if (res.status === 401) {
      handleLogout();
    }
    return;
  }
  const data = await res.json();
  historyList.innerHTML = "";
  for (const conv of data.conversations || []) {
    historyList.appendChild(renderHistoryItem(conv));
  }
}

async function deleteConversation(id) {
  const res = await authFetch(`${API_BASE}/conversations/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) return;
  if (conversationId === id) {
    messagesEl.innerHTML = "";
    summaryBox.textContent = "Summary: -";
    convMeta.textContent = "Conversation: -";
    conversationTitle.textContent = "Chat";
    conversationId = null;
    activeConversationId = null;
  }
  await loadConversations();
}

async function renameConversation(id, title) {
  const res = await authFetch(`${API_BASE}/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) return;
  if (conversationId === id) {
    await loadConversation();
  }
  await loadConversations();
}

async function loadMessages() {
  if (!conversationId) return;
  const res = await authFetch(`${API_BASE}/conversations/${conversationId}/messages`);
  if (!res.ok) return;
  const data = await res.json();
  messagesEl.innerHTML = "";
  const messages = [...(data.messages || [])].sort((a, b) => (a.id || 0) - (b.id || 0));
  for (const msg of messages) {
    addMessage(msg.role === "assistant" ? "ai" : "user", msg.content || "");
  }
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function fetchMe() {
  if (!authToken) {
    setAuthState(null);
    return;
  }
  const res = await authFetch(`${API_BASE}/auth/me`);
  if (!res.ok) {
    authToken = "";
    localStorage.removeItem("authToken");
    setAuthState(null);
    return;
  }
  const data = await res.json();
  setAuthState(data);
  await loadConversations();
}

function handleLogout() {
  authToken = "";
  localStorage.removeItem("authToken");
  setAuthState(null);
  messagesEl.innerHTML = "";
  conversationId = null;
  activeConversationId = null;
  historyList.innerHTML = "";
  convMeta.textContent = "Conversation: -";
  summaryBox.textContent = "Summary: -";
}

async function askQuestion() {
  if (!conversationId) {
    await createConversation();
  }
  const question = questionInput.value.trim();
  if (!question) return;

  addMessage("user", question);
  questionInput.value = "";

  const payload = {
    conversation_id: conversationId,
    question,
  };

  const { bubble, textEl, copyBtn } = addMessage("ai", "");
  const res = await authFetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => ({}));
    textEl.textContent = data.detail || "Request failed.";
    if (res.status === 401) {
      handleLogout();
    }
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let finalSources = null;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx = buffer.indexOf("\n\n");
    while (idx !== -1) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const lines = chunk.split("\n");
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        const dataStr = trimmed.replace(/^data:\s*/, "");
        if (!dataStr || dataStr === "[DONE]") continue;
        let payload;
        try {
          payload = JSON.parse(dataStr);
        } catch (err) {
          continue;
        }
        if (DEBUG_STREAM) {
          console.log("stream payload", payload);
        }
        if (payload.type === "delta") {
          appendText(textEl, payload.content || "");
        } else if (payload.type === "done") {
          finalSources = payload.sources || [];
          if (payload.answer) {
            textEl.dataset.raw = payload.answer;
          }
          textEl.innerHTML = renderMarkdown(textEl.dataset.raw || "");
          if (copyBtn) {
            copyBtn.classList.remove("hidden");
          }
        } else if (payload.type === "error") {
          textEl.textContent = payload.detail || "Request failed.";
          if (copyBtn) {
            copyBtn.classList.remove("hidden");
          }
        }
      }
      idx = buffer.indexOf("\n\n");
    }
  }

  if (finalSources && finalSources.length) {
    const sourceEl = document.createElement("div");
    sourceEl.className = "source";
    sourceEl.textContent = `Sources: ${finalSources.join(", ")}`;
    bubble.appendChild(sourceEl);
  }
  await loadConversation();
}

askBtn.addEventListener("click", askQuestion);
questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    askQuestion();
  }
});
logoutBtn.addEventListener("click", handleLogout);
newConvBtn.addEventListener("click", async () => {
  messagesEl.innerHTML = "";
  summaryBox.textContent = "Summary: -";
  conversationTitle.textContent = "Chat";
  convMeta.textContent = "Conversation: -";
  conversationId = null;
  activeConversationId = null;
  await loadConversations();
});

fetchMe();
