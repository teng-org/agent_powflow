<template>
  <div class="account-shell">
    <div class="account-rail fade-up">
      <div class="account-header">
        <div class="dot"></div>
        <div>
          <div class="eyebrow">Personal Center</div>
          <h1>账户与安全</h1>
          <p>管理基础信息与登录密码。</p>
        </div>
        <div class="account-user" v-if="authState.user">
          <span class="pill muted">当前账号：{{ authState.user.username }}</span>
          <span class="pill muted">展示名（仅管理员可改）：{{ authState.user.display_name || "未设置" }}</span>
        </div>
        <router-link class="btn ghost" to="/chat">返回</router-link>
      </div>

      <div class="section">
        <div class="section-head">
          <div>
            <div class="eyebrow">Profile</div>
            <h2>基础信息</h2>
          </div>
          <span class="pill">{{ saveStatus }}</span>
        </div>
        <div class="two-col">
          <label class="field slim">
            用户名
            <input v-model.trim="profile.username" type="text" placeholder="用户名" disabled />
          </label>
          <label class="field slim">
            展示名称（仅管理员可修改）
            <input v-model.trim="profile.display_name" type="text" placeholder="由管理员维护" disabled />
          </label>
          <label class="field slim">
            邮箱
            <input v-model.trim="profile.email" type="email" placeholder="name@example.com" />
          </label>
          <label class="field slim">
            电话
            <input v-model.trim="profile.phone" type="tel" placeholder="手机号" />
          </label>
          <label class="field slim">
            地区
            <input v-model.trim="profile.region" type="text" placeholder="地区" />
          </label>
        </div>
        <div class="actions">
          <button class="btn primary" @click="saveProfile" :disabled="savingProfile">
            {{ savingProfile ? "Saving..." : "保存资料" }}
          </button>
        </div>
      </div>

      <div class="section">
        <div class="section-head">
          <div>
            <div class="eyebrow">Security</div>
            <h2>登录密码</h2>
          </div>
          <span class="pill" :class="{ error: passwordStatus.includes('失败') }">{{ passwordStatus }}</span>
        </div>
        <div class="two-col">
          <label class="field slim">
            当前密码
            <input v-model.trim="passwordForm.current" type="password" placeholder="Current password" />
          </label>
          <label class="field slim">
            新密码
            <input v-model.trim="passwordForm.new" type="password" placeholder="New password" />
          </label>
        </div>
        <div class="actions">
          <button class="btn primary" @click="savePassword" :disabled="savingPassword">
            {{ savingPassword ? "Updating..." : "更新登录密码" }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, watchEffect, onMounted } from "vue";
import { useRouter } from "vue-router";
import {
  authState,
  fetchMe,
  updateProfile,
  changePassword,
} from "../lib/auth";

const router = useRouter();

const profile = reactive({
  username: "",
  display_name: "",
  email: "",
  phone: "",
  region: "",
});
const passwordForm = reactive({ current: "", new: "" });

const saveStatus = ref("未保存");
const passwordStatus = ref("未修改");
const savingProfile = ref(false);
const savingPassword = ref(false);

function syncProfile() {
  if (!authState.user) return;
  profile.username = authState.user.username || "";
  profile.display_name = authState.user.display_name || "";
  profile.email = authState.user.email || "";
  profile.phone = authState.user.phone || "";
  profile.region = authState.user.region || "";
}

watchEffect(syncProfile);

onMounted(async () => {
  if (!authState.checked) {
    await fetchMe();
  }
  if (!authState.user) {
    router.push("/");
    return;
  }
  syncProfile();
});

async function saveProfile() {
  savingProfile.value = true;
  saveStatus.value = "保存中...";
  // 展示名仅管理员可改，用户个人保存时不传 display_name/username
  const { email, phone, region } = profile;
  const res = await updateProfile({ email, phone, region });
  savingProfile.value = false;
  if (!res.ok) {
    saveStatus.value = "保存失败";
    return;
  }
  saveStatus.value = "已保存";
  syncProfile();
}

async function savePassword() {
  if (!passwordForm.current || !passwordForm.new) {
    passwordStatus.value = "请填写完整";
    return;
  }
  savingPassword.value = true;
  passwordStatus.value = "更新中...";
  const res = await changePassword(passwordForm.current, passwordForm.new);
  savingPassword.value = false;
  if (!res.ok) {
    passwordStatus.value = res.detail || "更新失败，请检查原密码";
    return;
  }
  passwordStatus.value = "更新成功";
  passwordForm.current = "";
  passwordForm.new = "";
}
</script>

<style scoped>
.account-shell {
  min-height: 100vh;
  background: radial-gradient(140% 140% at 0% 0%, #f7f9fc, #ffffff 60%, #f6f7fb);
  display: flex;
  justify-content: center;
  padding: 48px 16px 64px;
}

.account-rail {
  width: min(960px, 100%);
  padding: 12px 0 24px;
  display: grid;
  gap: 28px;
}

.account-header {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 16px;
  align-items: center;
  padding: 16px 0;
  border-bottom: 1px solid #e5e7eb;
}

.account-header h1 {
  margin: 4px 0 6px;
  font-size: 28px;
}

.account-header p {
  margin: 0;
  color: #4b5563;
}

.account-user {
  justify-self: end;
}

.dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  box-shadow: 0 0 0 6px rgba(99, 102, 241, 0.08);
}

.eyebrow {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #6b7280;
}

.section {
  background: white;
  border: 1px solid #eef1f4;
  border-radius: 16px;
  padding: 18px 20px 20px;
  box-shadow: 0 20px 40px rgba(15, 23, 42, 0.04);
  display: grid;
  gap: 14px;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.section-head h2 {
  margin: 6px 0 0;
}

.two-col {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}

.field.slim input {
  padding: 10px 12px;
}

.actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
}

.note {
  margin: 0;
}

.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 999px;
  background: #f3f4f6;
  color: #4a5568;
  font-size: 12px;
}

.pill.error {
  background: #ffecef;
  color: #b42318;
}

.pill.success {
  background: #e6fffa;
  color: #0f766e;
}

.muted {
  color: #6b7280;
}

@media (max-width: 720px) {
  .account-header {
    grid-template-columns: 1fr;
    align-items: start;
  }
  .account-user {
    justify-self: start;
  }
  .actions {
    justify-content: flex-start;
  }
}
</style>
