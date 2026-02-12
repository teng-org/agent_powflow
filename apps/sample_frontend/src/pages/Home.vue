<template>
  <div class="home-hero">
    <header class="hero-nav">
      <div class="hero-mark">K</div>
      <div class="hero-name">Knowledge Chat</div>
    </header>

    <main class="hero-main">
      <div class="hero-copy fade-up">
        <div class="eyebrow">Knowledge Workspace</div>
        <h1>让知识整理与对话，一气呵成。</h1>
        <p>
          上传文档、生成标签摘要、实时问答。为研发和运营打造的高效知识基座。
        </p>
        <div class="hero-actions">
          <button class="btn primary" @click="authState.user ? goChat() : openModal('login')">
            {{ authState.user ? "进入 Chat" : "登录 / 注册" }}
          </button>
          <button class="btn ghost" @click="openModal('register')">新用户注册</button>
        </div>
        <div class="hero-meta">
          <span>实时流式回答</span>
          <span>可视化库与文档管理</span>
          <span>向量检索 + 摘要生成</span>
        </div>
      </div>
    </main>
  </div>

  <div v-if="showModal" class="home-modal" @click.self="closeModal">
    <div class="home-modal-card fade-up">
      <div class="modal-head">
        <div>
          <div class="eyebrow">Knowledge Chat</div>
          <h3>{{ mode === "login" ? "登录" : "注册" }}</h3>
        </div>
        <button class="btn ghost" @click="closeModal">关闭</button>
      </div>
      <div class="status-pill">{{ statusText }}</div>
      <label class="field">
        Username
        <input v-model.trim="username" type="text" placeholder="yourname" />
      </label>
      <label class="field">
        Password
        <input v-model.trim="password" type="password" placeholder="password" />
      </label>
      <div class="modal-actions">
        <button class="btn primary" @click="handleSubmit">
          {{ mode === "login" ? "登录" : "注册" }}
        </button>
        <button class="btn ghost" @click="toggleMode">
          {{ mode === "login" ? "去注册" : "已有账号，去登录" }}
        </button>
        <button v-if="authState.user" class="btn soft" @click="goChat">直接进入 Chat</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from "vue";
import { useRouter } from "vue-router";
import { authState, fetchMe, login, register } from "../lib/auth";

const router = useRouter();
const username = ref("");
const password = ref("");
const statusText = ref("等待操作");
const showModal = ref(false);
const mode = ref("login");

onMounted(async () => {
  if (authState.token && !authState.checked) {
    await fetchMe();
  }
  if (authState.user) {
    statusText.value = `已登录：${authState.user.username}`;
  }
});

function openModal(nextMode = "login") {
  mode.value = nextMode;
  showModal.value = true;
  statusText.value = nextMode === "login" ? "登录以继续" : "注册新账号";
}

function closeModal() {
  showModal.value = false;
}

function toggleMode() {
  mode.value = mode.value === "login" ? "register" : "login";
  statusText.value = mode.value === "login" ? "登录以继续" : "注册新账号";
}

async function handleSubmit() {
  if (!username.value || !password.value) {
    statusText.value = "Username and password required";
    return;
  }
  statusText.value = mode.value === "login" ? "Signing in..." : "Creating account...";
  const fn = mode.value === "login" ? login : register;
  const res = await fn(username.value, password.value);
  if (!res.ok) {
    statusText.value = mode.value === "login" ? "Login failed" : "Register failed";
    return;
  }
  statusText.value = `Signed in as ${authState.user?.username || "user"}`;
  showModal.value = false;
  router.push("/chat");
}

function goChat() {
  router.push("/chat");
}
</script>

<style scoped>
.home-hero {
  min-height: 100vh;
  background: radial-gradient(120% 120% at 10% 20%, #eef2ff, #ffffff 50%, #f9fafb);
  padding: 32px clamp(16px, 4vw, 40px) 80px;
  display: flex;
  flex-direction: column;
}

.hero-nav {
  display: grid;
  grid-template-columns: auto auto 1fr;
  gap: 12px;
  align-items: center;
}

.hero-mark {
  width: 42px;
  height: 42px;
  border-radius: 12px;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  display: grid;
  place-items: center;
  color: white;
  font-weight: 800;
  font-size: 18px;
}

.hero-name {
  font-weight: 700;
  color: #111827;
}

.hero-main {
  flex: 1;
  display: grid;
  place-items: center;
  text-align: center;
}

.hero-copy {
  max-width: 720px;
  display: grid;
  gap: 16px;
}

.hero-copy h1 {
  margin: 0;
  font-size: clamp(32px, 6vw, 46px);
  line-height: 1.15;
}

.hero-copy p {
  margin: 0;
  color: #4b5563;
  font-size: 16px;
}

.hero-actions {
  display: flex;
  gap: 12px;
  justify-content: center;
  flex-wrap: wrap;
}

.hero-meta {
  display: flex;
  gap: 12px;
  justify-content: center;
  flex-wrap: wrap;
  color: #6b7280;
  font-size: 14px;
}

.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 12px;
  color: #6b7280;
}

.home-modal {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.35);
  display: grid;
  place-items: center;
  z-index: 30;
  padding: 16px;
}

.home-modal-card {
  width: min(480px, 100%);
  background: #fff;
  border-radius: 16px;
  padding: 20px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.12);
  display: grid;
  gap: 14px;
}

.modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.modal-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.status-pill {
  padding: 10px 12px;
  border-radius: 10px;
  background: #f7fafc;
  color: #4a5568;
  font-size: 14px;
}

.fade-up {
  animation: fadeUp 0.5s ease both;
}

@keyframes fadeUp {
  from {
    opacity: 0;
    transform: translateY(12px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 720px) {
  .hero-nav {
    grid-template-columns: auto auto 1fr auto;
  }
  .hero-main {
    align-items: start;
  }
}
</style>
