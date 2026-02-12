<template>
  <div class="chatgpt-page" :class="{ collapsed: isSidebarCollapsed }">
    <aside class="chatgpt-sidebar">
      <button class="chatgpt-brand" @click="goHome">
        <div class="chatgpt-logo">K</div>
        <div class="chatgpt-brand-text">
          <div class="chatgpt-title">Knowledge Chat</div>
          <div class="chatgpt-sub">Chat workspace</div>
        </div>
      </button>

      <button class="chatgpt-btn primary" @click="startNewConversation">
        + New chat
      </button>

      <div class="chatgpt-section">
        <div class="chatgpt-section-title">History</div>
        <div class="chatgpt-history">
          <div
            v-for="conv in conversations"
            :key="conv.conversation_id"
            class="chatgpt-history-item"
            :class="{ active: conv.conversation_id === activeConversationId }"
            @click="selectConversation(conv)"
          >
            <div class="chatgpt-history-row">
              <div
                class="chatgpt-history-title"
                :title="conv.title || conv.summary || `Conversation #${conv.conversation_id}`"
              >
                {{ conv.title || conv.summary || `Conversation #${conv.conversation_id}` }}
              </div>
              <div class="chatgpt-menu">
                <button
                  class="chatgpt-icon-btn"
                  @click.stop="toggleMenu(conv.conversation_id)"
                >
                  ⋯
                </button>
                <div
                  v-if="openMenuId === conv.conversation_id"
                  class="chatgpt-menu-panel"
                >
                  <button class="chatgpt-menu-item" @click="renameConversation(conv)">
                    Rename
                  </button>
                  <button class="chatgpt-menu-item" @click="deleteConversation(conv)">
                    Delete
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="chatgpt-user compact">
        <div class="chatgpt-user-inline">
          <div class="chatgpt-avatar">
            {{ (authState.user?.username || "?").slice(0, 2).toUpperCase() }}
          </div>
          <div class="chatgpt-user-name">
            {{ authState.user?.username || "Guest" }}
          </div>
        </div>
        <button class="chatgpt-icon-btn" @click.stop="toggleUserMenu">⋯</button>
        <div
          v-if="userMenuOpen && !isSidebarCollapsed"
          class="chatgpt-user-menu expanded"
          @click.stop
        >
          <button v-if="!authState.user" class="chatgpt-menu-item" @click="goHome">
            Login
          </button>
          <button v-else class="chatgpt-menu-item" @click="handleLogoutAndClose">
            Logout
          </button>
          <button class="chatgpt-menu-item" @click="startNewConversationAndClose">
            New chat
          </button>
          <button class="chatgpt-menu-item" @click="goAccountAndClose">个人中心</button>
        </div>
      </div>
      <button class="chatgpt-user-avatar-collapsed" @click.stop="toggleUserMenu">
        {{ (authState.user?.username || "?").slice(0, 2).toUpperCase() }}
      </button>
      <div
        v-if="userMenuOpen && isSidebarCollapsed"
        class="chatgpt-user-menu collapsed"
        @click.stop
      >
        <button v-if="!authState.user" class="chatgpt-menu-item" @click="goHome">Login</button>
        <button v-else class="chatgpt-menu-item" @click="handleLogoutAndClose">Logout</button>
        <button class="chatgpt-menu-item" @click="startNewConversationAndClose">
          New chat
        </button>
        <button class="chatgpt-menu-item" @click="goAccountAndClose">个人中心</button>
      </div>
    </aside>

    <button class="chatgpt-drawer-handle" @click="toggleSidebar">
      {{ isSidebarCollapsed ? ">" : "<" }}
    </button>

    <main class="chatgpt-main">
      <section ref="messagesRef" class="chatgpt-messages" @scroll="handleScroll">
        <div class="chatgpt-messages-inner">
          <div v-if="messages.length === 0" class="chatgpt-empty-state">
            <div class="chatgpt-empty-title">{{ currentGreeting.title }}</div>
            <div class="chatgpt-empty-sub">
              {{ currentGreeting.sub }}
            </div>
          </div>
          <div
            v-for="(message, index) in messages"
            :key="index"
            class="chatgpt-message"
            :class="message.role"
          >
            <div class="chatgpt-message-inner">
              <div class="chatgpt-bubble">
                <div v-if="message.role === 'ai'">
                  <div v-if="message.streaming && !message.content" class="typing-indicator">
                    <span></span>
                    <span></span>
                    <span></span>
                  </div>
                  <div v-else-if="message.streaming" class="chatgpt-stream">
                    <div v-html="renderMarkdown(message.content)"></div>
                    <span class="chatgpt-cursor">?</span>
                  </div>
                  <div v-else v-html="renderMarkdown(message.content)"></div>
                </div>
                <div v-else>{{ message.content }}</div>
              </div>
            </div>
            <div class="chatgpt-actions" :class="message.role">
              <button class="chatgpt-copy-btn" @click="copyMessage(message)">
                {{ message.copied ? "Copied" : "Copy" }}
              </button>
            </div>
          </div>
        </div>
      </section>

      <section class="chatgpt-composer floating fixed">
        <textarea
          ref="composerRef"
          v-model="question"
          :disabled="!authState.user || isSending"
          rows="2"
          placeholder="Ask anything..."
          @keydown.enter.exact.prevent="askQuestion"
        ></textarea>
        <button
          class="chatgpt-btn primary"
          :disabled="!authState.user || isSending"
          @click="askQuestion"
        >
          {{ isSending ? "Sending..." : "Send" }}
        </button>
      </section>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick, watch, computed } from "vue";
import { useRouter } from "vue-router";
import { authState, authFetch, fetchMe, logout } from "../lib/auth";
import { API_BASE } from "../lib/api";
import { renderMarkdown } from "../lib/markdown";

const messagesRef = ref(null);
const messages = ref([]);
const conversations = ref([]);
const question = ref("");
const conversationId = ref(null);
const activeConversationId = ref(null);
const conversationTitle = ref("");
const summary = ref("");
const isSending = ref(false);
const isSidebarCollapsed = ref(false);
const openMenuId = ref(null);
const userMenuOpen = ref(false);
const router = useRouter();
const composerRef = ref(null);
const MAX_COMPOSER_HEIGHT = 96;
const autoScrollEnabled = ref(true);
const replyGap = 140;
const greetings = [
  { title: "Hello there!", sub: "Ask your first question to start a conversation." },
  { title: "Need inspiration?", sub: "Try asking about docs, code, or an idea you're exploring." },
  { title: "Stuck on something?", sub: "Share a snippet or a question—I'll help break it down." },
];
const greetingIndex = ref(0);
const currentGreeting = computed(() => greetings[greetingIndex.value % greetings.length]);
let greetingTimer = null;
const handleGlobalClick = () => {
  closeUserMenu();
  closeHistoryMenu();
};
function closeUserMenu() {
  userMenuOpen.value = false;
}

function closeHistoryMenu() {
  openMenuId.value = null;
}

onMounted(async () => {
  document.body.classList.add("chatgpt-body");
  window.addEventListener("click", handleGlobalClick, false);
  greetingTimer = setInterval(() => {
    greetingIndex.value = (greetingIndex.value + 1) % greetings.length;
  }, 4000);
  if (!authState.checked) {
    await fetchMe();
  }
  if (authState.user) {
    await loadConversations();
  }
  adjustComposerHeight();
});

onUnmounted(() => {
  document.body.classList.remove("chatgpt-body");
  window.removeEventListener("click", handleGlobalClick, false);
  if (greetingTimer) {
    clearInterval(greetingTimer);
    greetingTimer = null;
  }
});

function scrollToBottom(gap = 0) {
  nextTick(() => {
    const el = messagesRef.value;
    if (!el) return;
    const applyScroll = () => {
      const target = el.scrollHeight - el.clientHeight - gap;
      el.scrollTop = Math.max(target, 0);
    };
    applyScroll();
    requestAnimationFrame(applyScroll);
    setTimeout(applyScroll, 60);
  });
}

function adjustComposerHeight() {
  nextTick(() => {
    const el = composerRef.value;
    if (!el) return;
    el.style.height = "auto";
    const nextHeight = Math.min(el.scrollHeight, MAX_COMPOSER_HEIGHT);
    el.style.height = `${nextHeight}px`;
    el.style.overflowY = el.scrollHeight > MAX_COMPOSER_HEIGHT ? "auto" : "hidden";
  });
}

watch(question, () => {
  adjustComposerHeight();
});

watch(
  () => messages.value.length,
  () => {
    if (autoScrollEnabled.value) {
      scrollToBottom();
    }
  },
  { flush: "post" }
);

function copyMessage(message) {
  navigator.clipboard?.writeText(message.content || "");
  message.copied = true;
  setTimeout(() => {
    message.copied = false;
  }, 1200);
}

async function createConversation() {
  const res = await authFetch(`${API_BASE}/conversations`, { method: "POST" });
  if (handleAuthFailure(res)) return null;
  if (!res.ok) return null;
  const data = await res.json();
  conversationId.value = data.conversation_id;
  activeConversationId.value = data.conversation_id;
  await loadConversationById(data.conversation_id);
  await loadConversations();
  return data.conversation_id;
}

async function loadConversationById(id) {
  if (!id) return;
  const res = await authFetch(`${API_BASE}/conversations/${id}`);
  if (handleAuthFailure(res)) return;
  if (!res.ok) return;
  const data = await res.json();
  summary.value = data.summary || "-";
  conversationTitle.value = data.title || "Chat";
}

async function loadConversation() {
  await loadConversationById(conversationId.value);
}

async function loadConversations() {
  const res = await authFetch(`${API_BASE}/conversations`);
  if (handleAuthFailure(res)) return;
  if (!res.ok) return;
  const data = await res.json();
  conversations.value = data.conversations || [];
}

async function loadMessagesById(id) {
  if (!id) return;
  const res = await authFetch(`${API_BASE}/conversations/${id}/messages`);
  if (handleAuthFailure(res)) return;
  if (!res.ok) return;
  const data = await res.json();
  const list = [...(data.messages || [])].sort(
    (a, b) => (a.id || 0) - (b.id || 0)
  );
  messages.value = list.map((msg) => ({
    role: msg.role === "assistant" ? "ai" : "user",
    content: msg.content || "",
    sources: msg.sources || [],
    loading: false,
    streaming: false,
  }));
  autoScrollEnabled.value = true;
  scrollToBottom();
}

async function loadMessages() {
  await loadMessagesById(conversationId.value);
}

async function startNewConversation() {
  messages.value = [];
  summary.value = "-";
  conversationTitle.value = "New chat";
  conversationId.value = null;
  activeConversationId.value = null;
  await loadConversations();
}

async function selectConversation(conv) {
  activeConversationId.value = conv.conversation_id;
  conversationId.value = conv.conversation_id;
  await loadConversation();
  await loadMessages();
  await loadConversations();
  autoScrollEnabled.value = true;
  scrollToBottom();
}

async function deleteConversation(conv) {
  if (!confirm("Delete this conversation?")) return;
  openMenuId.value = null;
  conversations.value = conversations.value.filter(
    (item) => item.conversation_id !== conv.conversation_id
  );
  const res = await authFetch(
    `${API_BASE}/conversations/${conv.conversation_id}`,
    { method: "DELETE" }
  );
  if (handleAuthFailure(res)) return;
  if (!res.ok && res.status !== 404) return;
  if (conversationId.value === conv.conversation_id) {
    await startNewConversation();
  }
  await loadConversations();
}

async function renameConversation(conv) {
  const nextTitle = prompt("Rename conversation", conv.title || "");
  if (nextTitle === null) return;
  const res = await authFetch(
    `${API_BASE}/conversations/${conv.conversation_id}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: nextTitle.trim() }),
    }
  );
  if (handleAuthFailure(res)) return;
  if (!res.ok) return;
  if (conversationId.value === conv.conversation_id) {
    await loadConversation();
  }
  await loadConversations();
}

async function askQuestion() {
  if (!authState.user || isSending.value) return;
  let currentConvId = conversationId.value;
  if (!currentConvId) {
    currentConvId = await createConversation();
    if (!currentConvId) return;
  }
  const startConversationId = currentConvId;
  const prompt = question.value.trim();
  if (!prompt) return;

  messages.value.push({ role: "user", content: prompt });
  question.value = "";
  messages.value.push({
    role: "ai",
    content: "",
    sources: [],
    loading: true,
    streaming: true,
  });
  // Grab the reactive proxy from the array so later mutations trigger re-render.
  const aiMessage = messages.value[messages.value.length - 1];
  autoScrollEnabled.value = true;
  scrollToBottom(replyGap);

  isSending.value = true;
  const res = await authFetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      conversation_id: startConversationId,
      question: prompt,
    }),
  });
  if (handleAuthFailure(res)) {
    aiMessage.content = "Please sign in again.";
    aiMessage.loading = false;
    aiMessage.streaming = false;
    isSending.value = false;
    return;
  }
  if (!res.ok || !res.body) {
    aiMessage.content = "Request failed.";
    aiMessage.loading = false;
    aiMessage.streaming = false;
    isSending.value = false;
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let finalSources = [];
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
        if (payload.type === "delta") {
          aiMessage.content += payload.content || "";
          aiMessage.loading = false;
          aiMessage.streaming = true;
          if (autoScrollEnabled.value) {
            scrollToBottom();
          }
        } else if (payload.type === "done") {
          finalSources = payload.sources || [];
          if (payload.answer) {
            aiMessage.content = payload.answer;
          }
          aiMessage.loading = false;
          aiMessage.streaming = false;
        } else if (payload.type === "error") {
          aiMessage.content = payload.detail || "Request failed.";
          aiMessage.loading = false;
          aiMessage.streaming = false;
        }
      }
      idx = buffer.indexOf("\n\n");
    }
  }
  aiMessage.sources = finalSources;
  isSending.value = false;
  await loadConversationById(startConversationId);
  await loadConversations();
  if (activeConversationId.value === startConversationId) {
    await loadMessagesById(startConversationId);
  }
}

function handleLogout() {
  logout();
  messages.value = [];
  conversationId.value = null;
  activeConversationId.value = null;
  conversations.value = [];
  summary.value = "-";
  conversationTitle.value = "New chat";
}

function handleAuthFailure(res) {
  if (res.status === 401) {
    handleLogout();
    return true;
  }
  return false;
}

function toggleSidebar() {
  isSidebarCollapsed.value = !isSidebarCollapsed.value;
}

function toggleMenu(id) {
  openMenuId.value = openMenuId.value === id ? null : id;
  if (openMenuId.value) {
    closeUserMenu();
  }
}

function toggleUserMenu() {
  userMenuOpen.value = !userMenuOpen.value;
  if (userMenuOpen.value) {
    openMenuId.value = null;
  }
}

function goAccount() {
  router.push("/account");
}

function goHome() {
  router.push("/");
  userMenuOpen.value = false;
}

function handleLogoutAndClose() {
  handleLogout();
  userMenuOpen.value = false;
}

function startNewConversationAndClose() {
  startNewConversation();
  userMenuOpen.value = false;
}

function goAccountAndClose() {
  goAccount();
  userMenuOpen.value = false;
}

function handleScroll() {
  const el = messagesRef.value;
  if (!el) return;
  const distanceToBottom = el.scrollHeight - el.clientHeight - el.scrollTop;
  autoScrollEnabled.value = distanceToBottom < 24;
}
</script>
