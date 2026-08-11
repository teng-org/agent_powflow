<script setup>
defineProps({
  kbState: { type: Object, required: true },
  isExecutionLoggedIn: { type: Boolean, required: true },
  createKb: { type: Function, required: true },
  loadKbs: { type: Function, required: true },
  getKbId: { type: Function, required: true },
  getKbName: { type: Function, required: true },
  selectKb: { type: Function, required: true },
  renameKb: { type: Function, required: true },
  deleteKb: { type: Function, required: true },
  bindKbTenant: { type: Function, required: true },
  loadKbBindings: { type: Function, required: true },
  unbindKbTenant: { type: Function, required: true },
})
</script>

<template>
  <div class="kb-col">
    <h3>知识库列表</h3>
    <div class="row">
      <input v-model="kbState.createName" placeholder="新知识库名称" />
      <button class="btn primary" :disabled="kbState.loading || !isExecutionLoggedIn" @click="createKb">创建</button>
      <button class="btn ghost" :disabled="!isExecutionLoggedIn" @click="loadKbs">刷新</button>
    </div>
    <div class="table-wrap short">
      <table>
        <thead><tr><th>名称</th><th>ID</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="item in kbState.items" :key="getKbId(item)" :class="{ selected: kbState.selectedKbId === getKbId(item) }">
            <td :title="getKbName(item)">{{ getKbName(item) }}</td>
            <td :title="getKbId(item)">{{ getKbId(item) }}</td>
            <td><div class="row tight">
              <button class="btn ghost" @click="selectKb(item)">进入</button>
              <button class="btn ghost" @click="renameKb(item)">重命名</button>
              <button class="btn ghost danger-text" @click="deleteKb(item)">删除</button>
            </div></td>
          </tr>
        </tbody>
      </table>
    </div>

    <h3>租户绑定</h3>
    <div class="row">
      <select v-model="kbState.bindTenantId">
        <option value="">选择租户</option>
        <option v-for="t in kbState.tenants" :key="t.tenantId" :value="t.tenantId">{{ t.tenantCode }} ({{ t.name }})</option>
      </select>
      <button class="btn ghost" :disabled="!kbState.selectedKbId || !kbState.bindTenantId" @click="bindKbTenant">绑定</button>
      <button class="btn ghost" :disabled="!kbState.selectedKbId" @click="loadKbBindings">刷新</button>
    </div>
    <div class="table-wrap mini">
      <table>
        <thead><tr><th>target_tenant_id</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="(b, idx) in kbState.bindings" :key="idx">
            <td>{{ b.target_tenant_id || b.targetTenantId || b.tenant_id || '-' }}</td>
            <td>
              <button class="btn ghost danger-text" @click="unbindKbTenant(b.target_tenant_id || b.targetTenantId || b.tenant_id)">解除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
