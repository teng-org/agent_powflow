<template>
  <div class="kb-login">
    <div class="kb-login-card fade-up">
      <div class="kb-login-left">
        <div class="badge">Knowledge Base</div>
        <h1>进入知识库</h1>
        <p class="muted">
          再次验证以保护你的文档与向量索引。默认密码与账号密码一致，可在个人中心单独设置。
        </p>
        <ul class="kb-bullets">
          <li>专属二级密码，防止误操作</li>
          <li>个人中心可重置知识库密码</li>
          <li>当前账号：{{ maskedUser }}</li>
        </ul>
      </div>

      <div class="kb-login-right">
        <label class="field">
          知识库密码
          <input
            v-model.trim="password"
            type="password"
            placeholder="输入知识库密码"
            @keydown.enter="handleLogin"
          />
        </label>

        <div class="kb-actions">
          <button class="btn primary full" @click="handleLogin" :disabled="loading">
            {{ loading ? "验证中..." : "登录知识库" }}
          </button>
          <button class="btn ghost full" @click="goChat">返回 Chat</button>
        </div>

        <div class="kb-status" :class="{ error: status.startsWith('验证失败') }">
          {{ status }}
        </div>
        <p class="muted small">提示：忘记密码可在个人中心重置知识库密码。</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { authState, kbLogin } from "../lib/auth";

const router = useRouter();
const route = useRoute();

const password = ref("");
const status = ref("等待验证");
const loading = ref(false);

const maskedUser = computed(() => {
  if (!authState.user?.username) return "未登录";
  const name = authState.user.username;
  if (name.length <= 2) return name;
  return `${name.slice(0, 1)}***${name.slice(-1)}`;
});

async function handleLogin() {
  if (!password.value) {
    status.value = "请输入密码";
    return;
  }
  loading.value = true;
  status.value = "正在验证...";
  const res = await kbLogin(password.value);
  loading.value = false;
  if (!res.ok) {
    status.value = "验证失败，请检查密码";
    return;
  }
  status.value = "登录成功，正在进入知识库...";
  router.push(route.query.redirect || "/knowledge");
}

function goChat() {
  router.push("/chat");
}
</script>

<style scoped>
.kb-login {
  min-height: 100vh;
  background: radial-gradient(140% 140% at 20% 20%, #edf2ff, #f9fafb 45%, #fef6e4);
  display: grid;
  place-items: center;
  padding: 48px 16px;
}

.kb-login-card {
  width: min(960px, 100%);
  background: white;
  border-radius: 20px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.08);
  display: grid;
  grid-template-columns: 1fr 1fr;
  overflow: hidden;
}

.kb-login-left {
  padding: 28px;
  background: linear-gradient(135deg, #f2f6ff, #eef2ff);
  border-right: 1px solid #eef2ff;
}

.kb-login-left h1 {
  margin: 12px 0 8px;
  font-size: 28px;
}

.kb-login-left p {
  margin: 0 0 12px;
  line-height: 1.5;
}

.kb-bullets {
  margin: 0;
  padding-left: 18px;
  color: #4a5568;
  line-height: 1.6;
  display: grid;
  gap: 6px;
  font-size: 14px;
}

.kb-login-right {
  padding: 28px;
  display: grid;
  gap: 14px;
  align-content: start;
}

.kb-actions {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.kb-actions .btn.full {
  width: 100%;
}

.kb-status {
  padding: 10px 12px;
  border-radius: 10px;
  background: #f7fafc;
  color: #4a5568;
  font-size: 14px;
}

.kb-status.error {
  background: #fff0f0;
  color: #c53030;
}

.muted.small {
  font-size: 12px;
  margin: 0;
}

@media (max-width: 900px) {
  .kb-login-card {
    grid-template-columns: 1fr;
  }
  .kb-login-left {
    border-right: none;
    border-bottom: 1px solid #eef2ff;
  }
}
</style>
