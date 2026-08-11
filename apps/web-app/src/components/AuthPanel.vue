<template>
  <main ref="authShell" class="auth-shell" :class="{ 'is-register': mode === 'register' }">
    <div class="auth-ambient" aria-hidden="true">
      <span class="auth-orb auth-orb-blue"></span>
      <span class="auth-orb auth-orb-violet"></span>
      <span class="auth-orb auth-orb-cyan"></span>
      <span class="auth-orbit auth-orbit-one"></span>
      <span class="auth-orbit auth-orbit-two"></span>
      <span class="auth-noise"></span>
    </div>

    <header class="auth-topbar">
      <button class="auth-brand" type="button" aria-label="返回登录" @click="switchMode('login')">
        <span class="auth-brand-mark">P</span>
        <span>POWFLOW</span>
      </button>
      <button class="auth-theme-toggle" type="button" :aria-label="theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'" @click="toggleTheme">
        <svg v-if="theme === 'dark'" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v2m0 14v2M3 12h2m14 0h2M5.64 5.64l1.42 1.42m9.88 9.88 1.42 1.42m0-12.72-1.42 1.42M7.06 16.94l-1.42 1.42M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z" /></svg>
        <svg v-else viewBox="0 0 24 24" aria-hidden="true"><path d="M20.5 14.2A8 8 0 0 1 9.8 3.5 8.5 8.5 0 1 0 20.5 14.2Z" /></svg>
      </button>
    </header>

    <Transition name="auth-scene" mode="out-in">
      <section v-if="mode === 'login'" key="login" class="auth-login-layout">
        <div class="auth-story">
          <p class="auth-eyebrow"><span></span> Agentic AI Workspace</p>
          <h1>把 AI 能力，<br /><em>变成你的产品。</em></h1>
          <p class="auth-lead">在一个工作空间里构建智能体、编排工作流、管理知识库，并通过 API 安全发布到任何应用。</p>
          <div class="auth-capabilities" aria-label="平台能力">
            <span>Agent</span><i></i><span>Workflow</span><i></i><span>Knowledge</span><i></i><span>API</span>
          </div>
          <div class="auth-signal-card" aria-hidden="true">
            <span class="auth-signal-dot"></span>
            <div><small>PLATFORM STATUS</small><strong>All systems operational</strong></div>
            <div class="auth-signal-wave"><b></b><b></b><b></b><b></b><b></b><b></b></div>
          </div>
        </div>

        <div class="auth-card auth-login-card">
          <div class="auth-card-heading">
            <span class="auth-card-kicker">欢迎回来</span>
            <h2>{{ inviteEnabled ? '登录并接受邀请' : '登录工作空间' }}</h2>
            <p>{{ inviteEnabled ? '先验证你的已有账户，再安全加入受邀工作空间。' : '使用你的账户继续构建。' }}</p>
          </div>

          <form class="auth-form" @submit.prevent="submitLogin">
            <label class="auth-field">
              <span>邮箱或用户名</span>
              <input v-model.trim="form.email" type="text" required maxlength="254" autocomplete="username" placeholder="name@company.com 或用户名" @input="credentialsChanged" />
            </label>
            <label class="auth-field">
              <span>密码</span>
              <span class="auth-password-field">
                <input v-model="form.password" :type="showPassword ? 'text' : 'password'" required minlength="8" autocomplete="current-password" placeholder="输入账户密码" @input="credentialsChanged" />
                <button type="button" :aria-label="showPassword ? '隐藏密码' : '显示密码'" @click="showPassword = !showPassword">{{ showPassword ? '隐藏' : '显示' }}</button>
              </span>
            </label>

            <fieldset v-if="workspaceOptions.length > 1" class="auth-workspace-picker">
              <legend>选择用于验证的工作空间</legend>
              <label v-for="workspace in workspaceOptions" :key="workspace.tenantCode" class="auth-workspace-option" :class="{ active: form.tenantCode === workspace.tenantCode }">
                <input v-model="form.tenantCode" type="radio" :value="workspace.tenantCode" />
                <span><strong>{{ workspace.workspaceName || workspace.tenantCode }}</strong><small>{{ workspace.tenantCode }}</small></span>
              </label>
            </fieldset>

            <Transition name="auth-reveal">
              <div v-if="inviteEnabled" class="auth-invite-panel">
                <div class="auth-invite-note"><span>i</span><p>邀请不会创建新账户。邮箱必须与邀请对象一致。</p></div>
                <label class="auth-field">
                  <span>邀请令牌</span>
                  <input v-model.trim="form.invitationToken" required autocomplete="off" placeholder="pwi_••••••••" />
                </label>
              </div>
            </Transition>

            <div v-if="displayError" class="auth-error" role="alert">{{ displayError }}</div>

            <div class="auth-form-meta">
              <label class="auth-check"><input :checked="remember" type="checkbox" @change="$emit('update:remember', $event.target.checked)" /><span>保持登录</span></label>
              <button class="auth-text-button" type="button" @click="toggleInvite">{{ inviteEnabled ? '返回普通登录' : '已有邀请？' }}</button>
            </div>

            <button class="auth-submit" type="submit" :disabled="loading">
              <span>{{ loading ? '正在验证…' : (inviteEnabled ? '登录并加入工作空间' : '进入工作空间') }}</span><b aria-hidden="true">→</b>
            </button>
          </form>

          <p class="auth-card-footer">还没有账户？<button type="button" @click="switchMode('register')">创建工作空间</button></p>
        </div>
      </section>

      <section v-else key="register" class="auth-register-layout">
        <div class="auth-card auth-register-card">
          <button class="auth-back" type="button" @click="switchMode('login')"><span>←</span> 返回登录</button>
          <div class="auth-card-heading auth-register-heading">
            <span class="auth-card-kicker">创建账户 · {{ registerStep }}/3</span>
            <h2>{{ registerTitles[registerStep - 1] }}</h2>
            <p>{{ registerDescriptions[registerStep - 1] }}</p>
          </div>

          <ol class="auth-progress" aria-label="注册进度">
            <li v-for="step in 3" :key="step" :class="{ active: step === registerStep, done: step < registerStep }"><span>{{ step < registerStep ? '✓' : step }}</span><b></b></li>
          </ol>

          <form ref="registerForm" class="auth-form" @submit.prevent="submitRegister">
            <Transition name="auth-step" mode="out-in">
              <div v-if="registerStep === 1" key="account" class="auth-step-fields">
                <label class="auth-field"><span>工作邮箱</span><input v-model.trim="form.email" type="email" required maxlength="254" autocomplete="username" placeholder="name@company.com" /></label>
                <label class="auth-field"><span>你的称呼</span><input v-model.trim="form.displayName" required maxlength="128" autocomplete="name" placeholder="团队成员会这样称呼你" /></label>
                <label class="auth-field"><span>设置密码</span><input v-model="form.password" type="password" required minlength="8" autocomplete="new-password" placeholder="至少 8 个字符" /></label>
              </div>

              <div v-else-if="registerStep === 2" key="workspace" class="auth-step-fields">
                <label class="auth-field"><span>工作空间名称</span><input v-model.trim="form.workspaceName" required maxlength="128" placeholder="例如：Acme AI 团队" /></label>
                <label class="auth-field">
                  <span>工作空间代码</span>
                  <input v-model.trim="form.tenantCode" required minlength="3" maxlength="63" pattern="[a-z0-9][a-z0-9-]{2,62}" autocomplete="off" placeholder="acme-ai" @input="normalizeWorkspaceCode" />
                  <small :class="{ bad: form.tenantCode && !workspaceCodeValid }">用于唯一识别工作空间，可使用小写字母、数字和连字符。</small>
                </label>
                <div class="auth-code-preview"><span>WORKSPACE URL</span><strong>powflow.ai/w/{{ form.tenantCode || 'your-workspace' }}</strong></div>
              </div>

              <div v-else key="confirm" class="auth-step-fields">
                <div class="auth-review">
                  <div><span>账户</span><strong>{{ form.displayName }}</strong><small>{{ form.email }}</small></div>
                  <div><span>工作空间</span><strong>{{ form.workspaceName }}</strong><small>{{ form.tenantCode }}</small></div>
                </div>
                <div class="auth-trust-note"><span>✓</span><p><strong>你始终拥有自己的数据</strong><small>现在不会产生费用。创建后可随时邀请团队成员。</small></p></div>
              </div>
            </Transition>

            <div v-if="displayError" class="auth-error" role="alert">{{ displayError }}</div>
            <div class="auth-register-actions">
              <button v-if="registerStep > 1" class="auth-secondary" type="button" @click="registerStep--">上一步</button>
              <button class="auth-submit" type="submit" :disabled="loading">
                <span>{{ loading ? '正在创建…' : (registerStep < 3 ? '继续' : '创建账户与工作空间') }}</span><b aria-hidden="true">→</b>
              </button>
            </div>
          </form>
          <p class="auth-legal">继续即表示你同意平台服务条款与隐私政策。</p>
        </div>
      </section>
    </Transition>
  </main>
</template>

<script setup>
import { computed, nextTick, reactive, ref, watch } from 'vue'

const props = defineProps({
  mode: { type: String, required: true },
  error: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  remember: { type: Boolean, default: true },
  appearance: { type: String, default: 'dark' },
  workspaceOptions: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:mode', 'update:remember', 'appearance-change', 'credentials-change', 'login', 'register'])
const registerForm = ref(null)
const authShell = ref(null)
const registerStep = ref(1)
const inviteEnabled = ref(false)
const showPassword = ref(false)
const localError = ref('')
const theme = computed(() => props.appearance === 'light' ? 'light' : 'dark')
const registerTitles = ['先认识一下你', '为团队创建空间', '确认后即可开始']
const registerDescriptions = ['用一个真实、常用的邮箱创建你的平台账户。', '工作空间用于隔离团队成员、数据和发布的 AI 能力。', '检查信息，一切准备就绪。']

const form = reactive({ email: '', displayName: '', workspaceName: '', invitationToken: '', password: '', tenantCode: '' })
const workspaceCodeValid = computed(() => /^[a-z0-9][a-z0-9-]{2,62}$/.test(form.tenantCode))
const displayError = computed(() => localError.value || props.error)

function switchMode(next) {
  localError.value = ''
  if (next === 'register') registerStep.value = 1
  emit('update:mode', next)
  nextTick(() => authShell.value?.scrollTo({
    top: 0,
    behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
  }))
}

function toggleTheme() {
  emit('appearance-change', theme.value === 'dark' ? 'light' : 'dark')
}

function toggleInvite() {
  inviteEnabled.value = !inviteEnabled.value
  localError.value = ''
  if (!inviteEnabled.value) form.invitationToken = ''
}

function submitLogin() {
  localError.value = ''
  emit('login', { ...form, acceptInvitation: inviteEnabled.value })
}

function submitRegister() {
  localError.value = ''
  const invalid = registerForm.value?.querySelector(':invalid')
  if (invalid) {
    invalid.reportValidity()
    return
  }
  if (registerStep.value === 2 && !workspaceCodeValid.value) {
    localError.value = '工作空间代码格式不正确'
    return
  }
  if (registerStep.value < 3) {
    registerStep.value += 1
    return
  }
  emit('register', { ...form })
}

function normalizeWorkspaceCode(event) {
  const normalized = String(event.target.value || '').toLowerCase().replace(/[\s_]+/g, '-').replace(/-{2,}/g, '-')
  form.tenantCode = normalized
  event.target.value = normalized
}

function credentialsChanged() {
  form.tenantCode = ''
  emit('credentials-change')
}

watch(() => props.workspaceOptions, (items) => {
  if (items.length > 1 && !items.some((item) => item.tenantCode === form.tenantCode)) form.tenantCode = items[0].tenantCode
}, { deep: true })

watch(() => props.mode, (value) => {
  localError.value = ''
  if (value === 'login') registerStep.value = 1
})
</script>
