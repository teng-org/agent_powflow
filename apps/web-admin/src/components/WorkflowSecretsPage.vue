<script setup>
import { onMounted, reactive, ref } from 'vue'

const props = defineProps({ fetchJson: { type: Function, required: true }, sectionDomId: { type: Function, required: true } })
const items = ref([])
const vaultAvailable = ref(false)
const loading = ref(false)
const error = ref('')
const message = ref('')
const form = reactive({ name: '', value: '', description: '' })
const rotate = reactive({ secret_id: '', value: '', description: '' })

function when(value) { return value ? new Date(value).toLocaleString() : '—' }
function clearNotice() { error.value = ''; message.value = '' }

async function load() {
  loading.value = true
  clearNotice()
  try {
    const response = await props.fetchJson('execution', '/api/v1/admin/workflow-secrets')
    items.value = response?.items || []
    vaultAvailable.value = Boolean(response?.vault_available)
  } catch (e) { error.value = e?.message || String(e) }
  finally { loading.value = false }
}

async function createSecret() {
  clearNotice()
  if (!form.name.trim() || !form.value) { error.value = '名称和值不能为空'; return }
  try {
    await props.fetchJson('execution', '/api/v1/admin/workflow-secrets', {
      method: 'POST', body: { name: form.name.trim(), value: form.value, description: form.description.trim() },
    })
    form.name = ''; form.value = ''; form.description = ''
    message.value = 'Secret 已加密保存；明文不会再次显示。'
    await load()
  } catch (e) { error.value = e?.message || String(e); form.value = '' }
}

function beginRotate(item) {
  clearNotice()
  rotate.secret_id = item.secret_id
  rotate.value = ''
  rotate.description = item.description || ''
}

async function rotateSecret() {
  if (!rotate.secret_id || !rotate.value) { error.value = '请输入新值'; return }
  clearNotice()
  try {
    await props.fetchJson('execution', `/api/v1/admin/workflow-secrets/${encodeURIComponent(rotate.secret_id)}`, {
      method: 'PUT', body: { value: rotate.value, description: rotate.description },
    })
    rotate.secret_id = ''; rotate.value = ''; rotate.description = ''
    message.value = 'Secret 已轮换，新版本立即生效。'
    await load()
  } catch (e) { error.value = e?.message || String(e); rotate.value = '' }
}

async function disableSecret(item) {
  if (!window.confirm(`停用 Secret“${item.name}”？引用它的新运行将失败。`)) return
  clearNotice()
  try {
    await props.fetchJson('execution', `/api/v1/admin/workflow-secrets/${encodeURIComponent(item.secret_id)}/disable`, { method: 'POST', body: {} })
    message.value = 'Secret 已停用。轮换新值可重新启用。'
    await load()
  } catch (e) { error.value = e?.message || String(e) }
}

async function deleteSecret(item) {
  if (!window.confirm(`永久删除已停用 Secret“${item.name}”？此操作不可恢复。`)) return
  clearNotice()
  try {
    await props.fetchJson('execution', `/api/v1/admin/workflow-secrets/${encodeURIComponent(item.secret_id)}`, { method: 'DELETE' })
    message.value = 'Secret 已永久删除。'
    await load()
  } catch (e) { error.value = e?.message || String(e) }
}

onMounted(load)
</script>

<template>
  <section class="content domain-section" :id="sectionDomId('main')" data-menu-section="main">
    <div class="panel hero">
      <div><div class="panel-title">Workflow Secrets</div><p class="panel-sub">为 HTTP 工具保存租户级凭据。服务只返回指纹和版本，绝不返回明文。</p></div>
      <button class="btn ghost" :disabled="loading" @click="load">{{ loading ? '加载中…' : '刷新' }}</button>
    </div>
    <p v-if="!vaultAvailable" class="banner warn">Vault 当前不可用。请配置 WORKFLOW_SECRET_MASTER_KEY 并重启 Execution API 与 Worker。</p>
    <p v-if="error" class="banner err">{{ error }}</p><p v-if="message" class="banner ok">{{ message }}</p>

    <div class="grid">
      <form class="panel" @submit.prevent="createSecret">
        <div class="panel-title">新建 Secret</div>
        <label>引用名称<input v-model.trim="form.name" autocomplete="off" placeholder="例如 billing-api" /></label>
        <label>Secret 值<input v-model="form.value" type="password" autocomplete="new-password" placeholder="保存后不可读取" /></label>
        <label>用途说明<textarea v-model="form.description" maxlength="500" rows="3" /></label>
        <button class="btn primary" :disabled="!vaultAvailable">加密保存</button>
      </form>
      <form v-if="rotate.secret_id" class="panel" @submit.prevent="rotateSecret">
        <div class="panel-title">轮换 Secret</div>
        <label>Secret ID<input :value="rotate.secret_id" disabled class="mono" /></label>
        <label>新值<input v-model="rotate.value" type="password" autocomplete="new-password" /></label>
        <label>用途说明<textarea v-model="rotate.description" maxlength="500" rows="3" /></label>
        <div class="actions"><button class="btn primary">确认轮换</button><button type="button" class="btn ghost" @click="rotate.secret_id = ''; rotate.value = ''">取消</button></div>
      </form>
    </div>

    <div class="panel list">
      <div class="panel-title">租户 Secret</div>
      <div class="table-wrap"><table><thead><tr><th>名称</th><th>状态</th><th>版本</th><th>指纹</th><th>说明</th><th>更新时间</th><th>操作</th></tr></thead><tbody>
        <tr v-for="item in items" :key="item.secret_id"><td><strong>{{ item.name }}</strong><small class="mono">{{ item.secret_id }}</small></td><td><span :class="['badge', item.status === 'ACTIVE' ? 'active' : 'disabled']">{{ item.status }}</span></td><td>v{{ item.version }}</td><td class="mono">{{ item.fingerprint }}</td><td>{{ item.description || '—' }}</td><td>{{ when(item.updated_at) }}</td><td><div class="actions"><button class="btn ghost" @click="beginRotate(item)">轮换</button><button class="btn danger" :disabled="item.status === 'DISABLED'" @click="disableSecret(item)">停用</button><button class="btn danger" :disabled="item.status !== 'DISABLED'" @click="deleteSecret(item)">删除</button></div></td></tr>
        <tr v-if="!items.length"><td colspan="7" class="empty" role="status" aria-live="polite" aria-atomic="true">暂无 Secret</td></tr>
      </tbody></table></div>
    </div>
  </section>
</template>

<style scoped>
.hero{display:flex;justify-content:space-between;gap:16px;align-items:start}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin:16px 0}.panel label{display:grid;gap:6px;margin:13px 0}.actions{display:flex;gap:8px;flex-wrap:wrap}.list{margin-top:16px}.table-wrap{overflow:auto}.mono{font-family:ui-monospace,monospace}.badge{display:inline-flex;padding:3px 8px;border-radius:99px;font-size:11px;font-weight:700}.badge.active{color:#16743a;background:#e6f6ec}.badge.disabled{color:#667085;background:#eef0f3}td small{display:block;margin-top:4px;color:var(--text-muted,#667085)}.empty{text-align:center;padding:24px}.banner.warn{background:#fff6db;color:#8a5a00}.banner.ok{background:#e8f7ed;color:#166534}.btn.danger{color:#b42318}@media(max-width:800px){.grid{grid-template-columns:1fr}.hero{align-items:stretch;flex-direction:column}}
</style>
