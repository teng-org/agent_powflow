<template>
  <div class="admin-login-shell">
    <div class="card">
      <h1>后台登录</h1>
      <p class="desc">仅限后台管理员。登录后进入 /system。</p>
      <label>
        用户名
        <input v-model="username" type="text" placeholder="admin" />
      </label>
      <label>
        密码
        <input v-model="password" type="password" placeholder="请输入密码" />
      </label>
      <button :disabled="loading" @click="submit">
        {{ loading ? "登录中..." : "登录" }}
      </button>
      <p v-if="error" class="error">{{ error }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { adminLogin, adminAuthState, adminLogout } from "../lib/auth";

const route = useRoute();
const router = useRouter();
const username = ref("");
const password = ref("");
const loading = ref(false);
const error = ref("");

async function submit() {
  error.value = "";
  loading.value = true;
  adminLogout();
  const res = await adminLogin(username.value, password.value);
  loading.value = false;
  if (!res.ok) {
    error.value = res.detail || "登录失败";
    return;
  }
  const redirect = route.query.redirect || "/system";
  router.replace(redirect);
}
</script>

<style scoped>
.admin-login-shell {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: #0c1118;
  color: #e5e9f0;
  padding: 24px;
}

.card {
  width: min(420px, 100%);
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 14px;
  padding: 20px;
  display: grid;
  gap: 12px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
}

h1 {
  margin: 0;
  font-size: 1.3rem;
  color: #f2f4f8;
}

.desc {
  margin: 0;
  color: #8fa0b3;
}

label {
  display: grid;
  gap: 6px;
  color: #9aa6b8;
  font-size: 0.95rem;
}

input {
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.05);
  color: #e5e9f0;
}

button {
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.15);
  background: linear-gradient(135deg, #1f3a60, #1a2a44);
  color: #e5e9f0;
  cursor: pointer;
}

button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.error {
  color: #ffb347;
  margin: 0;
}
</style>
