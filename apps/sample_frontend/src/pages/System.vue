<template>
  <div class="system-shell">
    <header class="system-topbar">
      <div class="brand">
        <div class="brand-mark">RG</div>
        <div>
          <div class="brand-title">企业级 RAG 管理与监控</div>
          <div class="brand-sub">API 基址：{{ API_BASE }}</div>
        </div>
      </div>
      <div class="top-actions">
        <div class="pill" :class="apiStatusClass">
          <span class="dot" />
          <span>{{ apiStatusLabel }}</span>
        </div>
        <div class="pill muted">
          当前管理员：{{ adminAuthState.user?.username || "未登录" }}
        </div>
        <div class="pill muted">
          上次同步：{{ lastUpdated ? formatTime(lastUpdated) : "未开始" }}
        </div>
        <button class="ghost-btn" :disabled="!adminAuthState.user" @click="goKnowledge">
          进入知识库
        </button>
        <button class="ghost-btn" :disabled="loading || isRefreshing" @click="refresh">
          {{ isRefreshing ? "同步中..." : "手动刷新" }}
        </button>
      </div>
    </header>

    <div class="system-layout">
      <aside class="system-nav">
        <div class="nav-title">监控导航</div>
        <button
          v-for="item in navItems"
          :key="item.key"
          class="nav-item"
          :class="{ active: activeSection === item.key }"
          @click="setSection(item.key)"
        >
          {{ item.label }}
        </button>
      </aside>

      <main class="system-main">
        <section v-if="activeSection === 'ops'" class="panel">
          <div class="panel-header">
            <div>
              <div class="section-title">运行指标</div>
              <p class="section-desc">指标全部来自后端 API，前端不推导业务逻辑。</p>
            </div>
            <div class="panel-actions">
              <label class="filter">
                窗口
                <select v-model="overviewWindow" @change="handleWindowChange">
                  <option value="1h">近 1 小时</option>
                  <option value="24h">近 24 小时</option>
                  <option value="7d">近 7 天</option>
                </select>
              </label>
              <div class="window-pill">生成时间：{{ overviewGeneratedAt }}</div>
            </div>
          </div>
          <div class="metric-grid">
            <article class="metric-card">
              <div class="metric-label">新建会话</div>
              <div class="metric-value">{{ overviewMetrics.new_conversations.value }}</div>
              <div class="metric-meta">
                样本：{{ overviewMetrics.new_conversations.sample_size || 0 }}；{{ overviewMetrics.new_conversations.note || "窗口内计数" }}
              </div>
            </article>
            <article class="metric-card">
              <div class="metric-label">待嵌入/待更新文档</div>
              <div class="metric-value">{{ overviewMetrics.pending_embeddings.value }}</div>
              <div class="metric-meta">
                样本：{{ overviewMetrics.pending_embeddings.sample_size || 0 }}；{{ overviewMetrics.pending_embeddings.note || "待嵌入 dirty 文档" }}
              </div>
            </article>
            <article class="metric-card">
              <div class="metric-label">知识库覆盖</div>
              <div class="metric-value">{{ overviewMetrics.knowledge_coverage.embedded }}/{{ overviewMetrics.knowledge_coverage.total }}</div>
              <div class="metric-meta">{{ overviewMetrics.knowledge_coverage.note || "embedded/total" }}</div>
            </article>
            <article class="metric-card">
              <div class="metric-label">注册用户</div>
              <div class="metric-value">{{ overviewMetrics.registered_users.value }}</div>
              <div class="metric-meta">数据源：{{ overviewMetrics.registered_users.source || "/api/users" }}</div>
            </article>
          </div>
        </section>

        <section v-else-if="activeSection === 'knowledge'" class="panel">
          <div class="panel-header">
            <div>
              <div class="section-title">知识库状态</div>
              <p class="section-desc">展示后端返回的库、文档与分片状态。</p>
            </div>
            <div class="panel-actions">
              <label class="filter">
                知识库
                <select v-model="selectedLibraryId">
                  <option value="all">全部</option>
                  <option v-for="lib in libraries" :key="lib.id" :value="String(lib.id)">{{ lib.name }}</option>
                  <option value="unassigned">未分配</option>
                </select>
              </label>
              <label class="filter">
                过滤
                <input v-model="docFilter" type="search" placeholder="按文件名或标签过滤" />
              </label>
            </div>
          </div>

          <div class="table-shell">
            <table class="data-table">
              <thead>
                <tr>
                  <th>文件</th>
                  <th>库</th>
                  <th @click="setDocSort('chunk_count')">分片数 <span class="sort" :class="sortIndicator('chunk_count')"></span></th>
                  <th @click="setDocSort('embedded_count')">已嵌入 <span class="sort" :class="sortIndicator('embedded_count')"></span></th>
                  <th @click="setDocSort('word_count')">词数 <span class="sort" :class="sortIndicator('word_count')"></span></th>
                  <th @click="setDocSort('size_bytes')">文件大小 <span class="sort" :class="sortIndicator('size_bytes')"></span></th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="doc in pagedDocs" :key="doc.id">
                  <td>
                    <div class="cell-main">{{ doc.filename }}</div>
                    <div class="cell-sub">标签：{{ doc.tag || "无" }}</div>
                  </td>
                  <td>{{ libraryName(doc.library_id) }}</td>
                  <td>{{ doc.chunk_count ?? 0 }}</td>
                  <td>{{ doc.embedded_count ?? 0 }}</td>
                  <td>{{ doc.word_count ?? 0 }}</td>
                  <td>{{ formatSize(doc.size_bytes) }}</td>
                  <td><span class="state-pill" :class="docStatusClass(doc)">{{ docStatusLabel(doc) }}</span></td>
                </tr>
                <tr v-if="!pagedDocs.length">
                  <td colspan="7" class="empty">无数据</td>
                </tr>
              </tbody>
            </table>
            <div class="table-footer">
              <div>共 {{ filteredDocs.length }} 条</div>
              <div class="pager">
                <button :disabled="docPage === 1" @click="docPage--">上一页</button>
                <span>{{ docPage }} / {{ totalDocPages }}</span>
                <button :disabled="docPage >= totalDocPages" @click="docPage++">下一页</button>
              </div>
            </div>
          </div>
        </section>

        <section v-else-if="activeSection === 'conversations'" class="panel">
          <div class="panel-header">
            <div>
              <div class="section-title">会话监控</div>
              <p class="section-desc">展示全部用户会话，可按用户名筛选、查看与删除。</p>
            </div>
            <div class="panel-actions">
              <input
                v-model="conversationUserFilter"
                type="search"
                placeholder="按用户名过滤"
                style="min-width: 180px"
              />
              <button class="ghost-btn" @click="loadConversations">筛选</button>
              <div class="window-pill">当前 {{ conversations.length }} 条</div>
            </div>
          </div>
          <div class="table-shell">
            <table class="data-table">
              <thead>
                <tr><th>ID</th><th>用户</th><th>标题</th><th>摘要</th><th>创建时间</th><th>操作</th></tr>
              </thead>
              <tbody>
                <tr v-for="conv in recentConversations" :key="conv.conversation_id">
                  <td class="mono">#{{ conv.conversation_id }}</td>
                  <td>{{ conv.username || "-" }}</td>
                  <td>{{ conv.title || "未命名" }}</td>
                  <td class="cell-sub">{{ conv.summary || "后端未返回摘要" }}</td>
                  <td>{{ conv.created_at ? formatTime(conv.created_at) : "未提供" }}</td>
                  <td>
                    <button class="ghost-btn" @click="viewConversation(conv)">查看</button>
                    <button class="ghost-btn danger" @click="removeConversation(conv)">删除</button>
                  </td>
                </tr>
                <tr v-if="!recentConversations.length">
                  <td colspan="6" class="empty">暂无数据</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section v-else class="panel">
          <div class="panel-header">
            <div>
              <div class="section-title">用户与权限</div>
              <p class="section-desc">数据源来自 /api/users，与后台管理员管理；普通用户不可访问知识库。</p>
            </div>
            <div class="panel-actions">
              <button class="ghost-btn" @click.stop="showPasswordModal = true">修改管理员密码</button>
              <button class="ghost-btn" @click.stop="showCreateUserModal = true">新增用户</button>
            </div>
          </div>
          <div class="table-shell">
            <table class="data-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>用户名</th>
                  <th>显示名</th>
                  <th>邮箱</th>
                  <th>角色</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="user in users" :key="user.id">
                  <td class="mono">#{{ user.id }}</td>
                  <td>{{ user.username }}</td>
                  <td><input v-model="user.display_name" class="inline-input" /></td>
                  <td><input v-model="user.email" class="inline-input" /></td>
                  <td>
                    <select v-model="user.role" class="inline-input">
                      <option value="viewer">viewer</option>
                      <option value="operator">operator</option>
                      <option value="admin">admin</option>
                    </select>
                  </td>
                  <td>
                    <select v-model="user.status" class="inline-input">
                      <option value="active">active</option>
                      <option value="disabled">disabled</option>
                    </select>
                  </td>
                  <td>
                    <button class="ghost-btn" @click="updateUser(user)">保存</button>
                    <button class="ghost-btn danger" @click="deleteUser(user)">删除</button>
                  </td>
                </tr>
                <tr v-if="!users.length">
                  <td colspan="7" class="empty">无用户数据</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
</main>
    </div>
  </div>

  <!-- 会话详情 -->
  <div v-if="conversationModalOpen" class="modal-mask">
    <div class="system-modal">
      <div class="modal-header">
        <div>
          <div class="section-title">会话详情</div>
          <p class="section-desc">
            会话 #{{ conversationDetail?.conversation_id || "-" }} · 用户：{{ conversationDetail?.username || "-" }}
          </p>
        </div>
        <button class="ghost-btn" @click="conversationModalOpen = false">X</button>
      </div>
      <div class="table-shell" style="max-height: 420px; overflow: auto">
        <table class="data-table">
          <thead>
            <tr><th>ID</th><th>角色</th><th>内容</th><th>时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="m in conversationMessages" :key="m.id">
              <td class="mono">#{{ m.id }}</td>
              <td>{{ m.role }}</td>
              <td class="cell-sub" style="max-width: 520px; white-space: pre-wrap">{{ m.content }}</td>
              <td>{{ m.created_at ? formatTime(m.created_at) : "-" }}</td>
            </tr>
            <tr v-if="!conversationMessages.length">
              <td colspan="4" class="empty">暂无消息</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- 顶部提示栏 -->
  <div class="notice-stack" v-if="notices.length">
    <div v-for="note in notices" :key="note.id" class="notice" :class="note.level">
      <div class="notice-text">{{ note.message }}</div>
      <button class="notice-close" @click="dismissNotice(note.id)">×</button>
    </div>
  </div>

  <!-- 管理员密码弹窗 -->
  <div v-if="showPasswordModal" class="modal-mask">
    <div class="system-modal">
      <div class="modal-header">
        <div>
          <div class="section-title">管理员密码</div>
          <p class="section-desc">首次登录 admin 时会显示默认密码，请立即修改并保存。</p>
        </div>
        <button class="ghost-btn" @click="closePasswordModal">X</button>
      </div>
      <form class="form-grid guarded-form" autocomplete="off" @submit.prevent>
        <div class="autofill-guard" aria-hidden="true">
          <input type="text" autocomplete="username" tabindex="-1" />
          <input type="password" autocomplete="new-password" tabindex="-1" />
        </div>
        <label class="filter">
          当前密码
          <input
            v-model="adminPasswordForm.current_password"
            type="password"
            name="admin-current-password"
            placeholder="当前密码"
            autocomplete="current-password"
            autocapitalize="off"
            spellcheck="false"
            data-1p-ignore
            data-lpignore="true"
          />
        </label>
        <label class="filter">
          新密码
          <input
            v-model="adminPasswordForm.new_password"
            type="password"
            name="admin-new-password"
            placeholder="新密码"
            autocomplete="new-password"
            autocapitalize="off"
            spellcheck="false"
            data-1p-ignore
            data-lpignore="true"
          />
        </label>
      </form>
      <div class="panel-actions">
        <button class="ghost-btn" @click="updateAdminPassword">修改密码</button>
        <span class="meta" v-if="adminPasswordMessage">{{ adminPasswordMessage }}</span>
      </div>
    </div>
  </div>

  <!-- 新增用户弹窗 -->
  <div v-if="showCreateUserModal" class="modal-mask">
    <div class="system-modal">
      <div class="modal-header">
        <div>
          <div class="section-title">新增用户</div>
          <p class="section-desc">仅后台管理员可创建/管理用户和权限。</p>
        </div>
        <button class="ghost-btn" @click="closeCreateUserModal">X</button>
      </div>
      <form class="form-grid guarded-form" autocomplete="off" @submit.prevent>
        <div class="autofill-guard" aria-hidden="true">
          <input type="text" autocomplete="username" tabindex="-1" />
          <input type="password" autocomplete="new-password" tabindex="-1" />
        </div>
        <label class="filter">
          用户名
          <input
            v-model="newUserForm.username"
            type="text"
            name="create-user-username"
            placeholder="必填"
            autocomplete="off"
            autocapitalize="off"
            spellcheck="false"
            data-1p-ignore
            data-lpignore="true"
          />
        </label>
        <label class="filter">
          密码
          <input
            v-model="newUserForm.password"
            type="password"
            name="create-user-password"
            placeholder="必填"
            autocomplete="new-password"
            autocapitalize="off"
            spellcheck="false"
            data-1p-ignore
            data-lpignore="true"
          />
        </label>
        <label class="filter">
          显示名
          <input
            v-model="newUserForm.display_name"
            type="text"
            name="create-user-display-name"
            placeholder="可选"
            autocomplete="off"
            autocapitalize="off"
            spellcheck="false"
            data-1p-ignore
            data-lpignore="true"
          />
        </label>
        <label class="filter">
          邮箱
          <input
            v-model="newUserForm.email"
            type="email"
            name="create-user-email"
            placeholder="可选"
            autocomplete="off"
            autocapitalize="off"
            spellcheck="false"
            data-1p-ignore
            data-lpignore="true"
          />
        </label>
        <label class="filter">
          角色
          <select v-model="newUserForm.role" autocomplete="off" name="create-user-role" data-1p-ignore data-lpignore="true">
            <option value="viewer">viewer</option>
            <option value="operator">operator</option>
            <option value="admin">admin</option>
          </select>
        </label>
        <label class="filter">
          状态
          <select v-model="newUserForm.status" autocomplete="off" name="create-user-status" data-1p-ignore data-lpignore="true">
            <option value="active">active</option>
            <option value="disabled">disabled</option>
          </select>
        </label>
      </form>
      <div class="panel-actions">
        <button class="ghost-btn" @click="createUser">创建用户</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { adminFetch, adminAuthState, fetchAdminMe } from "../lib/auth";
import { API_BASE } from "../lib/api";

const router = useRouter();
const loading = ref(false);
const isRefreshing = ref(false);
const error = ref("");
const overviewWindow = ref("24h");
const overviewData = ref(null);
const overviewError = ref("");
const lastUpdated = ref("");
const adminSecret = ref("");
const adminSecretShown = ref(false);
const adminPasswordForm = ref({ current_password: "", new_password: "" });
const adminPasswordMessage = ref("");
  const conversations = ref([]);
  const conversationMessages = ref([]);
  const conversationUserFilter = ref("");
  const conversationLoading = ref(false);
  const conversationDetail = ref(null);
  const conversationModalOpen = ref(false);
  const documents = ref([]);
const libraries = ref([]);
const users = ref([]);
const groups = ref([]);
const selectedLibraryId = ref("all");
const docFilter = ref("");
const docSortKey = ref("word_count");
const docSortDir = ref("desc");
const docPage = ref(1);
const pageSize = 8;
const activeSection = ref("ops");
const navItems = [
  { key: "ops", label: "运行指标" },
  { key: "knowledge", label: "知识库状态" },
  { key: "conversations", label: "会话监控" },
  { key: "org", label: "用户与权限" },
];
const newUserForm = ref({
  username: "",
  password: "",
  role: "viewer",
  status: "active",
  display_name: "",
  email: "",
});
const showPasswordModal = ref(false);
const showCreateUserModal = ref(false);
const notices = ref([]);
let noticeId = 0;

const overviewMetrics = computed(() => {
  const empty = {
    new_conversations: { value: 0, sample_size: 0, note: "" },
    registered_users: { value: 0, source: "/api/users" },
    pending_embeddings: { value: 0, sample_size: 0, note: "" },
    knowledge_coverage: { embedded: 0, total: 0, note: "" },
  };
  return overviewData.value?.metrics || empty;
});
const overviewGeneratedAt = computed(() => overviewData.value?.generated_at || "未生成");

const bannerWarnings = computed(() => {
  const list = [];
  overviewData.value?.system?.warnings?.forEach((w) => w?.message && list.push(w.message));
  if (overviewError.value) list.push(overviewError.value);
  if (error.value) list.push(error.value);
  if (adminSecret.value && !adminSecretShown.value) {
    list.push(`默认管理员密码：${adminSecret.value}（请立即保存并修改）`);
  }
  return list;
});

const apiStatusLabel = computed(() => {
  if (loading.value || isRefreshing.value) return "检查中";
  if (error.value || overviewError.value || bannerWarnings.value.length) return "可用但有告警";
  return "接口正常";
});
const apiStatusClass = computed(() => {
  if (loading.value || isRefreshing.value) return "info";
  if (error.value || overviewError.value || bannerWarnings.value.length) return "warn";
  return "success";
});

const filteredDocs = computed(() => {
  let list = documents.value.slice();
  if (selectedLibraryId.value === "unassigned") list = list.filter((d) => d.library_id === null);
  else if (selectedLibraryId.value !== "all") list = list.filter((d) => d.library_id === Number(selectedLibraryId.value));
  if (docFilter.value.trim()) {
    const kw = docFilter.value.trim().toLowerCase();
    list = list.filter((d) => (d.filename || "").toLowerCase().includes(kw) || (d.tag || "").toLowerCase().includes(kw));
  }
  const key = docSortKey.value;
  const desc = docSortDir.value === "desc";
  list.sort((a, b) => {
    const av = Number(a[key] ?? 0);
    const bv = Number(b[key] ?? 0);
    return desc ? bv - av : av - bv;
  });
  return list;
});
const totalDocPages = computed(() => Math.max(1, Math.ceil(filteredDocs.value.length / pageSize)));
const pagedDocs = computed(() => filteredDocs.value.slice((docPage.value - 1) * pageSize, (docPage.value) * pageSize));
const recentConversations = computed(() =>
  conversations.value
    .slice()
    .sort((a, b) => (b.created_at ? Date.parse(b.created_at) : 0) - (a.created_at ? Date.parse(a.created_at) : 0))
    .slice(0, 200)
);

function setDocSort(key) {
  if (docSortKey.value === key) docSortDir.value = docSortDir.value === "desc" ? "asc" : "desc";
  else {
    docSortKey.value = key;
    docSortDir.value = "desc";
  }
}
function sortIndicator(key) {
  if (docSortKey.value !== key) return "";
  return docSortDir.value === "desc" ? "desc" : "asc";
}
function libraryName(id) {
  if (id === null || id === undefined) return "未分配";
  const lib = libraries.value.find((l) => l.id === id);
  return lib ? lib.name : `库${id}`;
}
function docStatusClass(doc) {
  if (doc.embedding_dirty) return "warn";
  if ((doc.embedded_count || 0) < (doc.chunk_count || 0)) return "info";
  return "success";
}
function docStatusLabel(doc) {
  if (doc.embedding_dirty) return "待重嵌";
  if ((doc.embedded_count || 0) < (doc.chunk_count || 0)) return "待补齐";
  return "就绪";
}
function goKnowledge() {
  router.push("/knowledge");
}
function formatSize(bytes = 0) {
  if (!bytes || bytes < 1024) return `${bytes || 0} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
function formatTime(v) {
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? "无效时间" : d.toLocaleString();
}

function pushNotice(message, level = "info", duration = 5200) {
  if (!message) return;
  const id = noticeId++;
  notices.value.push({ id, message, level });
  setTimeout(() => dismissNotice(id), duration);
}

function dismissNotice(id) {
  notices.value = notices.value.filter((n) => n.id !== id);
}

async function refresh() {
  isRefreshing.value = true;
  await loadAllData();
  isRefreshing.value = false;
}
function setSection(key) {
  activeSection.value = key;
  window.scrollTo({ top: 0, behavior: "smooth" });
}
async function fetchAdminSecretOnce() {
  if (localStorage.getItem("adminSecretShown") === "true") {
    adminSecretShown.value = true;
    return;
  }
  try {
    const res = await adminFetch(`${API_BASE}/admin/init-secret`);
    if (res.ok) {
      const data = await res.json();
      adminSecret.value = data.password || "";
      adminSecretShown.value = false;
      localStorage.setItem("adminSecretShown", "true");
    }
  } catch (_) {}
}
async function updateAdminPassword() {
  adminPasswordMessage.value = "";
  if (!adminPasswordForm.value.current_password || !adminPasswordForm.value.new_password) {
    adminPasswordMessage.value = "请填写当前密码和新密码";
    pushNotice(adminPasswordMessage.value, "warn");
    return;
  }
  const res = await adminFetch(`${API_BASE}/admin/auth/password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(adminPasswordForm.value),
  });
  if (!res.ok) {
    let detail = "修改失败";
    try {
      const d = await res.json();
      detail = d.detail || detail;
    } catch (_) {}
    adminPasswordMessage.value = detail;
    pushNotice(detail, "error");
    return;
  }
  adminPasswordMessage.value = "修改成功，请重新登录生效";
  pushNotice(adminPasswordMessage.value, "success");
  adminPasswordForm.value.current_password = "";
  adminPasswordForm.value.new_password = "";
}
function handleWindowChange() {
  loadOverview();
}
async function loadOverview() {
  overviewError.value = "";
  try {
    const res = await adminFetch(`${API_BASE}/ops/metrics/overview?window=${overviewWindow.value}`);
    if (!res.ok) {
      overviewError.value = `接口返回 ${res.status}`;
      pushNotice(overviewError.value, "error");
      return;
    }
    const data = await res.json();
    overviewData.value = data;
    lastUpdated.value = data.generated_at || new Date().toISOString();
  } catch (err) {
    overviewError.value = err?.message || "加载运行指标失败";
    pushNotice(overviewError.value, "error");
  }
}
async function loadAllData() {
  error.value = "";
  loading.value = true;
  if (!adminAuthState.checked) await fetchAdminMe();
  if (!adminAuthState.user) {
    error.value = "Admin not logged in";
    pushNotice(error.value, "error");
    loading.value = false;
    return;
  }
  await fetchAdminSecretOnce();
  const failures = [];
  const tasks = [
    { name: "overview", fn: loadOverview },
    { name: "users", fn: loadUsers },
    { name: "groups", fn: loadGroups },
    { name: "libraries", fn: loadLibraries },
    { name: "documents", fn: loadDocuments },
    { name: "conversations", fn: loadConversations },
  ];
  for (const task of tasks) {
    try {
      await task.fn();
    } catch (err) {
      failures.push(`${task.name} load failed: ${err.message}`);
      pushNotice(`${task.name} 加载失败：${err.message}`, "error");
    }
  }
  if (failures.length) error.value = failures.join(" | ");
  lastUpdated.value = new Date().toISOString();
  docPage.value = 1;
  loading.value = false;
}
async function loadUsers() {
  const res = await adminFetch(`${API_BASE}/users`);
  if (!res.ok) throw new Error(`接口返回 ${res.status}`);
  const data = await res.json();
  users.value = data.users || [];
}
async function loadGroups() {
  const res = await adminFetch(`${API_BASE}/groups`);
  if (!res.ok) throw new Error(`接口返回 ${res.status}`);
  const data = await res.json();
  groups.value = data.groups || [];
}
async function loadLibraries() {
  const res = await adminFetch(`${API_BASE}/libraries`);
  if (!res.ok) throw new Error(`接口返回 ${res.status}`);
  const data = await res.json();
  libraries.value = data.libraries || [];
}
async function loadDocuments() {
  const res = await adminFetch(`${API_BASE}/documents`);
  if (!res.ok) throw new Error(`接口返回 ${res.status}`);
  const data = await res.json();
  documents.value = data.documents || [];
}
async function loadConversations() {
  conversationLoading.value = true;
  const qs = conversationUserFilter.value ? `?user_search=${encodeURIComponent(conversationUserFilter.value)}` : "";
  const res = await adminFetch(`${API_BASE}/conversations${qs}`);
  if (!res.ok) throw new Error(`接口返回 ${res.status}`);
  const data = await res.json();
  conversations.value = data.conversations || [];
  conversationLoading.value = false;
}

async function viewConversation(conv) {
  conversationDetail.value = conv;
  const res = await adminFetch(`${API_BASE}/conversations/${conv.conversation_id}/messages`);
  if (!res.ok) {
    pushNotice(`加载会话失败：${res.status}`, "error");
    return;
  }
  const data = await res.json();
  conversationMessages.value = data.messages || [];
  conversationModalOpen.value = true;
}

async function removeConversation(conv) {
  if (!confirm(`删除会话 #${conv.conversation_id} 吗？`)) return;
  const res = await adminFetch(`${API_BASE}/conversations/${conv.conversation_id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) {
    pushNotice(`删除失败：${res.status}`, "error");
    return;
  }
  await loadConversations();
}

async function createUser() {
  if (!newUserForm.value.username || !newUserForm.value.password) {
    error.value = "用户名和密码必填";
    pushNotice(error.value, "warn");
    return;
  }
  const payload = {
    username: newUserForm.value.username,
    password: newUserForm.value.password,
    role: newUserForm.value.role,
    status: newUserForm.value.status,
    display_name: newUserForm.value.display_name || undefined,
    email: newUserForm.value.email || undefined,
  };
  const res = await adminFetch(`${API_BASE}/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    error.value = `创建用户失败: ${res.status}`;
    pushNotice(error.value, "error");
    return;
  }
  closeCreateUserModal();
  pushNotice("用户创建成功", "success");
  await loadUsers();
}

async function updateUser(user) {
  const payload = {
    display_name: user.display_name,
    email: user.email,
    role: user.role,
    status: user.status,
  };
  const res = await adminFetch(`${API_BASE}/users/${user.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    error.value = `更新用户失败: ${res.status}`;
    pushNotice(error.value, "error");
    return;
  }
  pushNotice(`用户 ${user.username} 已更新`, "success");
  await loadUsers();
}

async function deleteUser(user) {
  const res = await adminFetch(`${API_BASE}/users/${user.id}`, { method: "DELETE" });
  if (!res.ok) {
    error.value = `删除用户失败: ${res.status}`;
    pushNotice(error.value, "error");
    return;
  }
  pushNotice(`用户 ${user.username} 已删除`, "success");
  await loadUsers();
}

onMounted(async () => {
  document.body.classList.add("system-body");
  await loadAllData();
});

onUnmounted(() => {
  document.body.classList.remove("system-body");
});

function closePasswordModal() {
  showPasswordModal.value = false;
  adminPasswordMessage.value = "";
  adminPasswordForm.value.current_password = "";
  adminPasswordForm.value.new_password = "";
}

function closeCreateUserModal() {
  showCreateUserModal.value = false;
  newUserForm.value = {
    username: "",
    password: "",
    role: "viewer",
    status: "active",
    display_name: "",
    email: "",
  };
}
</script>

<style scoped>
:global(:root) { --topbar-height: 72px; }
:global(body.system-body) { background: #0f1115; color: #dfe5ef; }
:global(body.system-body .app-shell) { padding: 0; min-height: 100vh; background: #0f1115; }
:global(body.system-body .bg-orb) { display: none; }
.system-shell { display:flex; flex-direction:column; min-height:100vh; background:#0f1115; color:#dfe5ef; font-family:"Segoe UI",sans-serif; }
.system-topbar { display:flex; align-items:center; justify-content:space-between; padding:12px 18px; border-bottom:1px solid #1f2a3a; background:#0f141c; height:var(--topbar-height); position:fixed; top:0; left:0; right:0; z-index:30; }
.brand { display:flex; align-items:center; gap:10px; }
.brand-mark { width:42px; height:42px; border-radius:8px; background:#1a2331; display:grid; place-items:center; font-weight:700; color:#e8edf5; }
.brand-title { font-size:1.05rem; font-weight:700; color:#f2f4f8; }
.brand-sub { color:#9aa6b8; font-size:0.9rem; }
.top-actions { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.pill { display:inline-flex; align-items:center; gap:8px; padding:8px 12px; border-radius:6px; background:#151b24; color:#dfe5ef; font-size:0.9rem; }
.pill.muted { background:#151b24; color:#9aa6b8; }
.pill.info { background:#152338; color:#7dc4ff; }
.pill.warn { background:#2c2412; color:#ffd166; }
.pill.success { background:#13281a; color:#7ed57a; }
.dot { width:8px; height:8px; border-radius:50%; background:currentColor; }
.ghost-btn { border:1px solid #2a3647; background:#141a22; color:#e5e9f0; padding:8px 12px; border-radius:6px; cursor:pointer; }
.ghost-btn:disabled { opacity:0.6; cursor:not-allowed; }
.ghost-btn.danger { border-color:rgba(255,99,99,0.4); color:#ff9f9f; }
.system-layout { display:flex; min-height:calc(100vh - var(--topbar-height)); margin-top:var(--topbar-height); }
.system-nav { width:240px; flex:0 0 240px; border-right:1px solid #1f2a3a; padding:16px 14px; background:#0f141c; display:grid; gap:8px; align-content:start; position:fixed; top:var(--topbar-height); bottom:0; left:0; overflow-y:auto; }
.nav-title { font-size:0.85rem; letter-spacing:0.08em; text-transform:uppercase; color:#7c879a; margin-bottom:4px; }
.nav-item { padding:9px 10px; border-radius:6px; color:#e5e9f0; background:#151b24; border:1px solid #1f2a3a; text-align:left; cursor:pointer; }
.nav-item:hover,.nav-item.active { border-color:#2c3b52; background:#19212d; }
.system-main { padding:16px 18px 24px; margin-left:240px; display:grid; gap:14px; width:100%; min-height:calc(100vh - var(--topbar-height)); }
.panel { background:#111721; border:1px solid #1f2a3a; border-radius:10px; padding:14px; display:grid; gap:12px; box-shadow:none; }
.panel-header { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; }
.section-title { font-size:1.02rem; font-weight:700; color:#f2f4f8; }
.section-desc { color:#8693a8; margin:2px 0 0; font-size:0.93rem; }
.window-pill { padding:8px 12px; border-radius:8px; background:#151b24; color:#9aa6b8; font-size:0.9rem; }
.metric-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; }
.metric-card { background:#121a25; border:1px solid #1f2a3a; border-radius:8px; padding:12px; display:grid; gap:6px; }
.metric-label { color:#9aa6b8; font-size:0.95rem; }
.metric-value { font-size:1.6rem; font-weight:700; color:#e5e9f0; }
.metric-meta { color:#7c879a; font-size:0.9rem; }
.panel-actions { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
.filter { display:grid; gap:4px; color:#9aa6b8; font-size:0.9rem; }
.filter input,.filter select { background:#151b24; border:1px solid #1f2a3a; border-radius:6px; color:#e5e9f0; padding:6px 10px; }
.table-shell { border:1px solid #1f2a3a; border-radius:8px; overflow:hidden; background:#0f141c; }
.data-table { width:100%; border-collapse:collapse; background:#0f141c; }
.data-table th,.data-table td { padding:10px 12px; border-bottom:1px solid #1f2a3a; text-align:left; color:#d8dee9; }
.data-table th { font-size:0.9rem; color:#9aa6b8; user-select:none; background:#111721; }
.data-table tbody tr:hover { background:#131a24; }
.cell-main { font-weight:600; }
.cell-sub { color:#7c879a; font-size:0.9rem; }
.state-pill { display:inline-flex; align-items:center; padding:4px 10px; border-radius:999px; font-size:0.85rem; color:#d8dee9; }
.state-pill.warn { background:#2c2412; color:#ffd166; }
.state-pill.info { background:#152338; color:#7dc4ff; }
.state-pill.success { background:#13281a; color:#7ed57a; }
.sort { display:inline-block; margin-left:6px; border:4px solid transparent; border-top-color:rgba(255,255,255,0.3); transform:translateY(2px); }
.sort.asc { border-top-color:transparent; border-bottom-color:rgba(255,255,255,0.7); }
.table-footer { display:flex; justify-content:space-between; align-items:center; padding:10px 12px; background:#111721; color:#9aa6b8; }
.pager { display:flex; gap:8px; align-items:center; }
.pager button { background:#151b24; border:1px solid #1f2a3a; border-radius:6px; color:#e5e9f0; padding:6px 10px; cursor:pointer; }
.pager button:disabled { opacity:0.5; cursor:not-allowed; }
.empty { text-align:center; color:#7c879a; padding:14px; }
.org-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:12px; }
.org-card { border:1px solid #1f2a3a; border-radius:8px; padding:12px; background:#111721; }
.org-title { font-weight:700; color:#e5e9f0; margin-bottom:8px; }
.org-list { display:grid; gap:10px; }
.org-item { padding:10px 12px; border-radius:8px; background:#151b24; border:1px solid #1f2a3a; }
.mono { font-family:"Consolas","SFMono-Regular",monospace; }
:global(html) { scroll-behavior:smooth; }
.modal-mask { position:fixed; top:0; left:0; right:0; bottom:0; width:100%; height:100%; background:rgba(0,0,0,0.55); display:grid; place-items:center; align-content:center; justify-content:center; z-index:200; padding:0; }
.system-modal { width:min(500px,90vw); max-height:80vh; overflow:auto; background:#111721; border:1px solid #1f2a3a; border-radius:10px; padding:16px; display:grid; gap:12px; box-shadow:0 12px 40px rgba(0,0,0,0.35); }
.modal-header { display:flex; justify-content:space-between; align-items:center; gap:10px; }
.autofill-guard { position:absolute; left:-9999px; width:1px; height:1px; overflow:hidden; opacity:0; pointer-events:none; }
.guarded-form { position:relative; }
.notice-stack { position:fixed; top:calc(var(--topbar-height) + 10px); left:50%; transform:translateX(-50%); display:grid; gap:8px; width:min(560px,calc(100% - 32px)); z-index:400; pointer-events:none; }
.notice { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:10px 14px; border-radius:10px; border:1px solid #2a3647; background:rgba(17,23,33,0.95); color:#e5e9f0; box-shadow:0 10px 30px rgba(0,0,0,0.35); pointer-events:auto; }
.notice.info { border-color:rgba(125,196,255,0.5); background:rgba(21,35,56,0.95); }
.notice.warn { border-color:rgba(255,209,102,0.5); background:rgba(44,36,18,0.95); color:#ffe8a3; }
.notice.error { border-color:rgba(255,99,99,0.55); background:rgba(60,20,20,0.95); color:#ffc4c4; }
.notice.success { border-color:rgba(126,213,122,0.55); background:rgba(19,40,26,0.95); color:#c5f0c3; }
.notice-text { flex:1; font-size:0.95rem; }
.notice-close { border:1px solid #2a3647; background:transparent; color:inherit; border-radius:6px; padding:4px 8px; cursor:pointer; }
.inline-input { width:100%; background:#151b24; border:1px solid #1f2a3a; border-radius:4px; color:#e5e9f0; padding:4px 8px; font-size:0.9rem; }
@media (max-width: 1024px) {
  .system-layout { flex-direction: column; }
  .system-nav { position: relative; top: 0; left: 0; right: 0; bottom: auto; width: 100%; margin: 0; grid-auto-flow: column; grid-auto-columns: 1fr; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); overflow-x: auto; overflow-y: hidden; }
  .system-main { margin-left: 0; }
}
@media (max-width: 1024px) {
  .system-layout { flex-direction: column; }
  .system-nav { position: relative; top: 0; left: 0; right: 0; bottom: auto; width: 100%; margin: 0; grid-auto-flow: column; grid-auto-columns: 1fr; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); overflow-x: auto; overflow-y: hidden; }
  .system-main { margin-left: 0; }
}
</style>
