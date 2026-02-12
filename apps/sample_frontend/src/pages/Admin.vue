<template>
  <div class="page admin-layout">
    <aside class="card panel fade-up">
      <div class="panel-header">
        <div>
          <div class="section-title">知识库</div>
          <strong>库管理</strong>
        </div>
        <div class="stack-row">
          <router-link class="btn ghost" to="/system">返回后台</router-link>
          <router-link class="btn ghost" to="/chat">返回 Chat</router-link>
        </div>
      </div>

      <div class="panel-header">
        <input v-model="librarySearch" placeholder="Search libraries" />
        <select v-model="librarySort">
          <option value="id_asc">ID asc</option>
          <option value="id_desc">ID desc</option>
          <option value="name_asc">Name A-Z</option>
          <option value="name_desc">Name Z-A</option>
        </select>
      </div>

      <button
        class="btn primary"
        :class="{ loading: isLibBusy('new') }"
        :disabled="isLibBusy('new')"
        @click="createLibrary"
      >
        {{ isLibBusy("new") ? "Creating..." : "New Library" }}
      </button>

      <div class="list">
        <div
          class="list-item"
          :class="{ active: activeLibraryId === null }"
          @click="setActiveLibrary(null)"
        >
          <strong>All Libraries</strong>
          <div class="meta">Browse everything</div>
        </div>
        <div
          v-for="lib in filteredLibraries"
          :key="lib.id"
          class="list-item"
          :class="{ active: lib.id === activeLibraryId }"
          @click="setActiveLibrary(lib.id)"
        >
          <div class="list-row">
            <span class="id-chip">#{{ lib.id }}</span>
            <strong class="list-title">{{ lib.name }}</strong>
          </div>
          <div class="meta">tag: {{ lib.tag || "-" }}</div>
          <div
            class="meta summary-line"
            :class="{ truncated: !isLibrarySummaryExpanded(lib) }"
            @click.stop="lib.summary && toggleLibrarySummary(lib)"
            :title="lib.summary || '-'"
          >
            summary: {{ lib.summary || "-" }}
          </div>
          <button
            v-if="lib.summary"
            class="link-btn"
            @click.stop="toggleLibrarySummary(lib)"
            style="margin-bottom: 6px"
          >
            {{ isLibrarySummaryExpanded(lib) ? "收起" : "展开" }}
          </button>
          <div style="display: flex; gap: 8px; margin-top: 10px">
            <button
              class="btn ghost"
              :class="{ loading: isLibBusy(lib.id) || hasLibJob(lib.id) }"
              :disabled="isLibBusy(lib.id) || hasLibJob(lib.id)"
              @click.stop="autoTagLibrary(lib)"
            >
              {{
                isLibBusy(lib.id) || hasLibJob(lib.id)
                  ? "Tagging..."
                  : "Auto Tag"
              }}
            </button>
            <button
              class="btn ghost"
              :class="{ loading: isLibBusy(lib.id) }"
              :disabled="isLibBusy(lib.id)"
              @click.stop="deleteLibrary(lib)"
            >
              {{ isLibBusy(lib.id) ? "Deleting..." : "Delete" }}
            </button>
          </div>
        </div>
      </div>
    </aside>

    <section class="card panel fade-up" style="animation-delay: 0.08s">
      <div class="panel-header">
        <div>
          <div class="section-title">文档</div>
          <strong>Collection</strong>
          <div class="meta">点击文档在全屏视图中查看 chunks</div>
        </div>
        <button class="btn ghost" @click="refreshAll">Refresh</button>
      </div>

      <div class="panel-header panel-header-tight">
        <div class="stack-row">
          <input v-model="documentSearch" placeholder="Search docs" />
          <select v-model="documentSort">
            <option value="id_asc">ID asc</option>
            <option value="id_desc">ID desc</option>
            <option value="name_asc">Name A-Z</option>
            <option value="name_desc">Name Z-A</option>
            <option value="status_dirty">Needs re-embed</option>
          </select>
        </div>
        <div class="stack-row">
          <select v-model="uploadLibraryId">
            <option value="">Select library</option>
            <option
              v-for="lib in libraries"
              :key="lib.id"
              :value="String(lib.id)"
            >
              {{ lib.name }} (#{{ lib.id }})
            </option>
          </select>
          <input
            ref="uploadFileRef"
            type="file"
            accept=".pdf,.docx,.txt,.md,.markdown,.html,.htm"
            multiple
          />
          <button
            class="btn soft"
            :class="{ loading: uploadBusy }"
            :disabled="uploadBusy"
            @click="uploadDocument"
        >
          {{ uploadBusy ? "Uploading..." : "Upload" }}
        </button>
      </div>
      </div>

      <div class="list">
        <div v-if="!filteredDocuments.length" class="list-item">
          No documents
        </div>
        <div
          v-for="doc in filteredDocuments"
          :key="doc.id"
          class="list-item"
          :class="{ active: doc.id === activeDocumentId }"
          @click="handleDocClick(doc, $event)"
        >
          <div class="list-row">
            <span class="id-chip">#{{ doc.id }}</span>
            <strong class="list-title">{{ doc.filename }}</strong>
          </div>
          <div class="tag-row">
            <span class="badge" :class="doc.embedding_dirty ? 'badge-warn' : 'badge-ok'">
              {{ doc.embedding_dirty ? "needs re-embed" : "ok" }}
            </span>
            <span class="badge" :class="hasChunks(doc) ? 'badge-info' : 'badge-muted'">
              chunks {{ doc.chunk_count }}
            </span>
            <span
              class="badge"
              :class="hasEmbedded(doc) ? 'badge-success' : 'badge-muted'"
            >
              embedded {{ doc.embedded_count }}
            </span>
          </div>
          <div class="meta">tag: {{ doc.tag || "-" }}</div>
          <div class="meta">
            size: {{ formatFileSize(doc.size_bytes) }} · words: {{ doc.word_count || 0 }}
          </div>
          <div
            class="meta summary-line"
            :class="{ truncated: !isSummaryExpanded(doc) }"
            @click.stop="doc.summary && toggleSummary(doc)"
            :title="doc.summary || '-'"
          >
            summary: {{ doc.summary || "-" }}
          </div>
          <button
            v-if="doc.summary"
            class="link-btn"
            @click.stop="toggleSummary(doc)"
            style="margin-bottom: 6px"
          >
            {{ isSummaryExpanded(doc) ? "收起" : "展开" }}
          </button>
          <div style="display: flex; gap: 8px; flex-wrap: wrap">
            <button
              class="btn ghost"
              :class="{ loading: isDocBusyWith(doc.id, 'auto') || hasDocJob(doc.id) }"
              :disabled="!hasEmbedded(doc) || isDocBusy(doc.id) || hasDocJob(doc.id)"
              @click.stop="autoTagDocument(doc)"
            >
              {{
                isDocBusyWith(doc.id, "auto") || hasDocJob(doc.id)
                  ? "Tagging..."
                  : hasMeta(doc)
                  ? "Re Tag"
                  : "Auto Tag"
              }}
            </button>
            <button
              class="btn ghost"
              :class="{ loading: isDocBusyWith(doc.id, 'chunk') }"
              :disabled="hasChunks(doc) || isDocBusy(doc.id)"
              @click.stop="chunkDocument(doc)"
            >
              {{ isDocBusyWith(doc.id, "chunk") ? "Chunking..." : "Chunk" }}
            </button>
            <button
              class="btn ghost"
              :class="{ loading: isDocBusyWith(doc.id, 'embed') }"
              :disabled="!hasChunks(doc) || isDocBusy(doc.id)"
              @click.stop="embedDocument(doc)"
            >
              {{
                isDocBusyWith(doc.id, "embed")
                  ? "Embedding..."
                  : doc.embedding_dirty
                  ? "Re-embed"
                  : hasEmbedded(doc)
                  ? "Embed again"
                  : "Embed"
              }}
            </button>
            <button
              class="btn ghost"
              :class="{ loading: isDocBusyWith(doc.id, 'del_chunks') }"
              :disabled="!hasChunks(doc) || isDocBusy(doc.id)"
              @click.stop="deleteChunks(doc)"
            >
              {{
                isDocBusyWith(doc.id, "del_chunks") ? "Deleting..." : "Delete chunks"
              }}
            </button>
            <button
              class="btn ghost"
              :class="{ loading: isDocBusyWith(doc.id, 'del_doc') }"
              :disabled="isDocBusy(doc.id)"
              @click.stop="deleteDocument(doc)"
            >
              {{ isDocBusyWith(doc.id, "del_doc") ? "Deleting..." : "Delete doc" }}
            </button>
            <button
              class="btn ghost"
              :class="{ loading: isDocBusyWith(doc.id, 'move') }"
              :disabled="isDocBusy(doc.id)"
              @click.stop="openMoveModal(doc)"
            >
              {{ isDocBusyWith(doc.id, "move") ? "Moving..." : "Move" }}
            </button>
          </div>
        </div>
      </div>
    </section>

    <div v-if="moveModalOpen" class="modal" @click.self="closeMoveModal">
      <div class="modal-card">
        <div class="panel-header">
          <strong>选择目标库</strong>
          <button class="btn ghost" @click="closeMoveModal">Close</button>
        </div>
        <div class="stack-row">
          <select v-model.number="moveTargetLibraryId">
            <option
              v-for="lib in libraries"
              :key="lib.id"
              :value="lib.id"
            >
              {{ lib.name }} (#{{ lib.id }})
            </option>
          </select>
          <button
            class="btn primary"
            :class="{ loading: isDocBusyWith(moveDocId, 'move') }"
            :disabled="!moveDocId || !moveTargetLibraryId || isDocBusy(moveDocId)"
            @click="confirmMove"
          >
            {{ isDocBusyWith(moveDocId, "move") ? "Moving..." : "Move" }}
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="setDelModalOpen"
      class="modal"
      @click.self="deletePasswordSet ? cancelDeleteModal() : null"
    >
      <div class="modal-card">
        <div class="panel-header">
          <strong>设置删除密码</strong>
          <button
            v-if="deletePasswordSet"
            class="btn ghost"
            @click="cancelDeleteModal"
          >
            Close
          </button>
        </div>
        <div class="stack-row column">
          <label class="field" v-if="deletePasswordSet">
            当前删除密码
            <input v-model="delCurrent" type="password" placeholder="输入当前删除密码" />
          </label>
          <label class="field">
            新删除密码
            <input v-model="delPass1" type="password" placeholder="大小写+数字+符号，至少8位" />
          </label>
          <label class="field">
            确认删除密码
            <input v-model="delPass2" type="password" placeholder="再次输入" />
          </label>
          <div class="meta">{{ delStatus }}</div>
          <button class="btn primary" @click="handleSetDeletePassword">保存</button>
        </div>
      </div>
    </div>

    <div v-if="verifyDelModalOpen" class="modal" @click.self="cancelDeleteModal">
      <div class="modal-card">
        <div class="panel-header">
          <strong>验证删除密码</strong>
          <button class="btn ghost" @click="cancelDeleteModal">Close</button>
        </div>
        <div class="stack-row column">
          <label class="field">
            删除密码
            <input v-model="delVerifyPass" type="password" placeholder="输入删除密码" />
          </label>
          <div class="meta">{{ delStatus }}</div>
          <button class="btn primary" @click="handleVerifyDeletePassword">验证</button>
        </div>
      </div>
    </div>
  </div>

  <div v-if="notices.length" class="notice-stack">
    <div
      v-for="note in notices"
      :key="note.id"
      class="notice"
      :class="note.level"
      @mouseenter="note.timer && clearTimeout(note.timer)"
      @mouseleave="note.timer && (note.timer = setTimeout(() => dismissNotice(note.id), 1800))"
    >
      <div class="notice-text">{{ note.message }}</div>
      <button class="notice-close" @click="dismissNotice(note.id)">×</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";
import { useRouter } from "vue-router";
import { API_BASE } from "../lib/api";
import {
  authFetch,
  adminFetch,
  adminAuthState,
} from "../lib/auth";

const router = useRouter();

const libraries = ref([]);
const documents = ref([]);

const librarySearch = ref("");
const librarySort = ref("id_asc");
const documentSearch = ref("");
const documentSort = ref("id_asc");
const uploadLibraryId = ref("");
const uploadFileRef = ref(null);

const activeLibraryId = ref(null);
const activeDocumentId = ref(null);
const moveModalOpen = ref(false);
const moveDocId = ref(null);
const moveTargetLibraryId = ref(null);
const setDelModalOpen = ref(false);
const verifyDelModalOpen = ref(false);
const deletePasswordSet = ref(true);
const delPass1 = ref("");
const delPass2 = ref("");
const delCurrent = ref("");
const delVerifyPass = ref("");
const delStatus = ref("");
let pendingDelResolve = null;
const summaryExpanded = ref({});
const librarySummaryExpanded = ref({});
const docBusy = ref({});
const docAction = ref({});
const libBusy = ref({});
const uploadBusy = ref(false);
const notices = ref([]);
let noticeId = 0;
const docJobMap = ref({});
const libJobMap = ref({});
const jobPollTimer = ref(null);
const DOC_JOB_KEY = "autoMetaDocJobs";
const LIB_JOB_KEY = "autoMetaLibJobs";
const jobStatusMap = ref({});

const ACTIVE_LIBRARY_KEY = "activeLibraryId";

onMounted(async () => {
  loadJobMaps();
  const stored = Number(localStorage.getItem(ACTIVE_LIBRARY_KEY));
  if (!Number.isNaN(stored)) {
    activeLibraryId.value = stored;
  }
  await fetchLibraries();
  await fetchDocuments();
  await hydrateJobsFromServer();
  startJobPolling();
});

const filteredLibraries = computed(() => {
  const search = librarySearch.value.trim().toLowerCase();
  let list = libraries.value.filter((lib) => {
    if (!search) return true;
    const target = `${lib.name} ${lib.tag || ""}`.toLowerCase();
    return target.includes(search);
  });
  list = list.slice().sort((a, b) => sortLibrary(a, b, librarySort.value));
  return list;
});

const filteredDocuments = computed(() => {
  const search = documentSearch.value.trim().toLowerCase();
  let list = documents.value.filter((doc) => {
    if (!search) return true;
    const target = `${doc.filename} ${doc.tag || ""}`.toLowerCase();
    return target.includes(search);
  });
  list = list.slice().sort((a, b) => sortDocument(a, b, documentSort.value));
  return list;
});

function sortLibrary(a, b, sort) {
  if (sort === "id_asc") return a.id - b.id;
  if (sort === "id_desc") return b.id - a.id;
  if (sort === "name_asc") return a.name.localeCompare(b.name);
  if (sort === "name_desc") return b.name.localeCompare(a.name);
  return 0;
}

function sortDocument(a, b, sort) {
  if (sort === "id_asc") return a.id - b.id;
  if (sort === "id_desc") return b.id - a.id;
  if (sort === "name_asc") return a.filename.localeCompare(b.filename);
  if (sort === "name_desc") return b.filename.localeCompare(a.filename);
  if (sort === "status_dirty") {
    return (b.embedding_dirty ? 1 : 0) - (a.embedding_dirty ? 1 : 0);
  }
  return 0;
}

async function fetchLibraries() {
  const res = await adminFetch(`${API_BASE}/libraries`);
  const data = await res.json();
  libraries.value = data.libraries || [];
  if (
    activeLibraryId.value !== null &&
    !libraries.value.some((lib) => lib.id === activeLibraryId.value)
  ) {
    activeLibraryId.value = null;
    localStorage.removeItem(ACTIVE_LIBRARY_KEY);
  }
  trimLibJobs(libraries.value);
}

async function fetchDocuments() {
  const url =
    activeLibraryId.value !== null
      ? `${API_BASE}/documents?library_id=${activeLibraryId.value}`
      : `${API_BASE}/documents`;
  const res = await adminFetch(url);
  const data = await res.json();
  documents.value = data.documents || [];
  trimDocJobs(documents.value);
}

async function hydrateJobsFromServer() {
  try {
    const res = await adminFetch(`${API_BASE}/jobs`);
    if (!res.ok) return;
    const data = await res.json();
    const active = (data || []).filter(
      (j) => j.status === "queued" || j.status === "running"
    );
    active.forEach((j) => {
      if (j.job_type === "auto_tag_document" && j.target_id) {
        trackDocJob(j.target_id, j.task_id);
        jobStatusMap.value[j.task_id] = j.status;
      }
      if (j.job_type === "auto_tag_library" && j.target_id) {
        trackLibJob(j.target_id, j.task_id);
        jobStatusMap.value[j.task_id] = j.status;
      }
    });
  } catch (_) {
    // ignore
  }
}

async function fetchDeleteStatus() {
  deletePasswordSet.value = true;
}

async function setActiveLibrary(libraryId) {
  activeLibraryId.value = libraryId;
  if (libraryId === null) {
    localStorage.removeItem(ACTIVE_LIBRARY_KEY);
  } else {
    localStorage.setItem(ACTIVE_LIBRARY_KEY, String(libraryId));
  }
  await fetchDocuments();
}

async function selectDocument(doc) {
  activeDocumentId.value = doc.id;
  router.push({ name: "knowledge-doc", params: { id: doc.id } });
}

async function handleDocClick(doc, event) {
  const selection = window.getSelection?.();
  if (selection && !selection.isCollapsed) return;
  await selectDocument(doc);
}

async function refreshAll() {
  await fetchLibraries();
  await fetchDocuments();
}

function forceSetDeletePassword() {
  setDelModalOpen.value = true;
  delStatus.value = "首次使用，需要设置删除密码";
}

function openChangeDeletePassword() {
  delStatus.value = deletePasswordSet.value
    ? "修改删除密码"
    : "首次使用，需要设置删除密码";
  setDelModalOpen.value = true;
}

function ensureDeleteAuth() {
  return Promise.resolve(true);
}

async function handleSetDeletePassword() {
  deletePasswordSet.value = true;
  setDelModalOpen.value = false;
  delStatus.value = "";
  delCurrent.value = "";
  delPass1.value = "";
  delPass2.value = "";
  if (pendingDelResolve) {
    pendingDelResolve(true);
    pendingDelResolve = null;
  }
}

async function handleVerifyDeletePassword() {
  delStatus.value = "";
  delVerifyPass.value = "";
  verifyDelModalOpen.value = false;
  if (pendingDelResolve) {
    pendingDelResolve(true);
    pendingDelResolve = null;
  }
}

function cancelDeleteModal() {
  if (setDelModalOpen.value && !deletePasswordSet.value) return;
  setDelModalOpen.value = false;
  verifyDelModalOpen.value = false;
  if (pendingDelResolve) {
    pendingDelResolve(false);
    pendingDelResolve = null;
  }
}

async function createLibrary() {
  const name = prompt("Library name?");
  if (!name) return;
  await withLibBusy("new", async () => {
    await adminFetch(`${API_BASE}/libraries`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    await fetchLibraries();
  });
}

async function deleteLibrary(lib) {
  if (!confirm("Delete this library and all its documents and embeddings?")) return;
  const ok = await ensureDeleteAuth();
  if (!ok) return;
  await withLibBusy(lib.id, async () => {
    await adminFetch(`${API_BASE}/libraries/${lib.id}`, { method: "DELETE" });
  });
  if (activeLibraryId.value === lib.id) {
    activeLibraryId.value = null;
    localStorage.removeItem(ACTIVE_LIBRARY_KEY);
  }
  await refreshAll();
}

async function autoTagLibrary(lib) {
  await withLibBusy(lib.id, async () => {
    const res = await adminFetch(`${API_BASE}/libraries/${lib.id}/auto-meta`, { method: "POST" });
    if (!res.ok) {
      const detail = await safeDetail(res, "Auto tag failed");
      pushNotice(detail, "error");
      return;
    }
    const data = await res.json();
    const jobId = data?.task_id || data?.job_id;
    if (jobId) {
      trackLibJob(lib.id, jobId);
      pushNotice(`库 #${lib.id} 已提交标签任务 (${jobId})`, "info");
    } else {
      pushNotice(`库 #${lib.id} 已提交标签任务`, "info");
    }
  });
}

async function chunkDocument(doc) {
  await withDocBusy(doc.id, "chunk", async () => {
    const res = await adminFetch(`${API_BASE}/documents/${doc.id}/chunk`, { method: "POST" });
    if (!res.ok) {
      const detail = await safeDetail(res, "Chunk 失败");
      pushNotice(detail, "error");
      return;
    }
    pushNotice(`文档 #${doc.id} 已切分`, "success");
    await fetchDocuments();
  });
}

async function embedDocument(doc) {
  await withDocBusy(doc.id, "embed", async () => {
    const res = await adminFetch(`${API_BASE}/documents/${doc.id}/embed`, { method: "POST" });
    if (!res.ok) {
      const detail = await safeDetail(res, "Embed 失败");
      pushNotice(detail, "error");
      return;
    }
    pushNotice(`文档 #${doc.id} 已完成 Embed/Re-embed`, "success");
    await fetchDocuments();
  });
}

async function deleteChunks(doc) {
  if (!confirm("Delete all chunks and embeddings for this document?")) return;
  const ok = await ensureDeleteAuth();
  if (!ok) return;
  await withDocBusy(doc.id, "del_chunks", async () => {
    await adminFetch(`${API_BASE}/documents/${doc.id}/chunks`, { method: "DELETE" });
    await fetchDocuments();
  });
}

async function deleteDocument(doc) {
  if (!confirm("Delete this document and all its chunks?")) return;
  const ok = await ensureDeleteAuth();
  if (!ok) return;
  await withDocBusy(doc.id, "del_doc", async () => {
    await adminFetch(`${API_BASE}/documents/${doc.id}`, { method: "DELETE" });
    await fetchDocuments();
  });
  if (activeDocumentId.value === doc.id) {
    activeDocumentId.value = null;
  }
}

function openMoveModal(doc) {
  moveDocId.value = doc.id;
  moveTargetLibraryId.value = doc.library_id || null;
  moveModalOpen.value = true;
}

function closeMoveModal() {
  moveModalOpen.value = false;
  moveDocId.value = null;
  moveTargetLibraryId.value = null;
}

async function moveDocument(docId, targetLibraryId) {
  if (!docId || !targetLibraryId) return;
  await withDocBusy(docId, "move", async () => {
    await adminFetch(`${API_BASE}/documents/${docId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ library_id: targetLibraryId }),
    });
    await fetchDocuments();
  });
}

async function confirmMove() {
  await moveDocument(moveDocId.value, moveTargetLibraryId.value);
  closeMoveModal();
}

function isDocBusy(docId) {
  return !!docBusy.value[docId];
}

function isLibBusy(id) {
  return !!libBusy.value[id];
}

function hasDocJob(docId) {
  const taskId = (docJobMap.value || {})[docId];
  if (!taskId) return false;
  const status = jobStatusMap.value?.[taskId];
  if (status && status !== "queued" && status !== "running") {
    removeDocJob(docId);
    return false;
  }
  return true;
}

function hasLibJob(libId) {
  const taskId = (libJobMap.value || {})[libId];
  if (!taskId) return false;
  const status = jobStatusMap.value?.[taskId];
  if (status && status !== "queued" && status !== "running") {
    removeLibJob(libId);
    return false;
  }
  return true;
}

function isDocBusyWith(docId, label) {
  return !!docBusy.value[docId] && docAction.value[docId] === label;
}

async function withDocBusy(docId, label, fn) {
  docBusy.value = { ...docBusy.value, [docId]: true };
  docAction.value = { ...docAction.value, [docId]: label };
  try {
    await fn();
  } finally {
    docBusy.value = { ...docBusy.value, [docId]: false };
    docAction.value = { ...docAction.value, [docId]: null };
  }
}

async function withLibBusy(id, fn) {
  libBusy.value = { ...libBusy.value, [id]: true };
  try {
    await fn();
  } finally {
    libBusy.value = { ...libBusy.value, [id]: false };
  }
}

async function autoTagDocument(doc) {
  await withDocBusy(doc.id, "auto", async () => {
    const res = await adminFetch(`${API_BASE}/documents/${doc.id}/auto-meta`, { method: "POST" });
    if (!res.ok) {
      const detail = await safeDetail(res, "Auto tag 失败");
      pushNotice(detail, "error");
      return;
    }
    const data = await res.json();
    const jobId = data?.task_id || data?.job_id;
    if (jobId) {
      trackDocJob(doc.id, jobId);
      pushNotice(`文档 #${doc.id} 已提交标签任务 (${jobId})`, "info");
    } else {
      pushNotice(`文档 #${doc.id} 已提交标签任务`, "info");
    }
  });
}

function toggleSummary(doc) {
  summaryExpanded.value = {
    ...summaryExpanded.value,
    [doc.id]: !summaryExpanded.value[doc.id],
  };
}

function isSummaryExpanded(doc) {
  return !!summaryExpanded.value[doc.id];
}

function toggleLibrarySummary(lib) {
  librarySummaryExpanded.value = {
    ...librarySummaryExpanded.value,
    [lib.id]: !librarySummaryExpanded.value[lib.id],
  };
}

function isLibrarySummaryExpanded(lib) {
  return !!librarySummaryExpanded.value[lib.id];
}

function hasChunks(doc) {
  return Number(doc?.chunk_count || 0) > 0;
}

function hasEmbedded(doc) {
  return Number(doc?.embedded_count || 0) > 0;
}

function hasMeta(doc) {
  return !!(doc?.tag || doc?.summary);
}

function formatFileSize(bytes) {
  const n = Number(bytes || 0);
  if (n >= 1024 * 1024 * 1024) return (n / (1024 * 1024 * 1024)).toFixed(2) + " GB";
  if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(2) + " MB";
  if (n >= 1024) return (n / 1024).toFixed(2) + " KB";
  return `${n} B`;
}

async function uploadDocument() {
  const files = Array.from(uploadFileRef.value?.files || []);
  if (!files.length) return;
  if (!uploadLibraryId.value) {
    alert("Please select a library for this document.");
    return;
  }
  uploadBusy.value = true;
  try {
    await Promise.all(
      files.map(async (file) => {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("library_id", uploadLibraryId.value);
        const res = await adminFetch(`${API_BASE}/documents/upload`, {
          method: "POST",
          body: formData,
        });
        if (!res.ok) {
          const detail = await safeDetail(res, `上传 ${file.name} 失败`);
          pushNotice(detail, "error");
          throw new Error(detail);
        }
        const data = await res.json();
        pushNotice(`已上传 ${file.name}（后台解析中）`, "success");
        if (data?.task_id && data?.document_id) {
          trackDocJob(data.document_id, data.task_id);
        }
      })
    );
    uploadFileRef.value.value = "";
    await fetchDocuments();
  } finally {
    uploadBusy.value = false;
  }
}

async function safeDetail(res, fallback) {
  try {
    const data = await res.json();
    return data?.detail || `${fallback}（${res.status}）`;
  } catch (_) {
    return `${fallback}（${res.status}）`;
  }
}

function pushNotice(message, level = "info", duration = 5200) {
  if (!message) return;
  const id = noticeId++;
  notices.value.push({ id, message, level });
  const timer = setTimeout(() => dismissNotice(id), duration);
  notices.value = notices.value.map((n) => (n.id === id ? { ...n, timer } : n));
}

function dismissNotice(id) {
  notices.value = notices.value.filter((n) => n.id !== id);
}

function loadJobMaps() {
  try {
    docJobMap.value = JSON.parse(localStorage.getItem(DOC_JOB_KEY) || "{}");
  } catch (_) {
    docJobMap.value = {};
  }
  try {
    libJobMap.value = JSON.parse(localStorage.getItem(LIB_JOB_KEY) || "{}");
  } catch (_) {
    libJobMap.value = {};
  }
}

function persistJobs() {
  localStorage.setItem(DOC_JOB_KEY, JSON.stringify(docJobMap.value || {}));
  localStorage.setItem(LIB_JOB_KEY, JSON.stringify(libJobMap.value || {}));
}

function trackDocJob(docId, taskId) {
  docJobMap.value = { ...(docJobMap.value || {}), [docId]: taskId };
  persistJobs();
  startJobPolling();
}

function trackLibJob(libId, taskId) {
  libJobMap.value = { ...(libJobMap.value || {}), [libId]: taskId };
  persistJobs();
  startJobPolling();
}

function removeDocJob(docId) {
  if (!docJobMap.value) return;
  const next = { ...(docJobMap.value || {}) };
  delete next[docId];
  docJobMap.value = next;
  persistJobs();
}

function removeLibJob(libId) {
  if (!libJobMap.value) return;
  const next = { ...(libJobMap.value || {}) };
  delete next[libId];
  libJobMap.value = next;
  persistJobs();
}

function trimDocJobs(docs) {
  if (!docJobMap.value) return;
  let changed = false;
  for (const [key, taskId] of Object.entries(docJobMap.value)) {
    const docId = Number(key);
    const doc = docs.find((d) => Number(d.id) === docId);
    if (!doc) continue;
    const hasMeta = (doc.summary && doc.summary.trim()) || (doc.tag && doc.tag.trim());
    if (hasMeta) {
      removeDocJob(docId);
      changed = true;
    }
  }
  if (changed) startJobPolling();
}

function trimLibJobs(libs) {
  if (!libJobMap.value) return;
  let changed = false;
  for (const [key, taskId] of Object.entries(libJobMap.value)) {
    const libId = Number(key);
    const lib = libs.find((l) => Number(l.id) === libId);
    if (!lib) continue;
    const hasMeta = (lib.summary && lib.summary.trim()) || (lib.tag && lib.tag.trim());
    if (hasMeta) {
      removeLibJob(libId);
      changed = true;
    }
  }
  if (changed) startJobPolling();
}

function startJobPolling() {
  if (jobPollTimer.value) return;
  jobPollTimer.value = setInterval(pollJobs, 5000);
  pollJobs();
}

function stopJobPolling() {
  if (jobPollTimer.value) {
    clearInterval(jobPollTimer.value);
    jobPollTimer.value = null;
  }
}

async function pollJobs() {
  const tasks = [];
  Object.entries(docJobMap.value || {}).forEach(([docId, taskId]) =>
    tasks.push({ kind: "doc", target: Number(docId), taskId })
  );
  Object.entries(libJobMap.value || {}).forEach(([libId, taskId]) =>
    tasks.push({ kind: "lib", target: Number(libId), taskId })
  );
  if (!tasks.length) return;

  let needDocRefresh = false;
  let needLibRefresh = false;

  for (const item of tasks) {
    try {
      const res = await adminFetch(`${API_BASE}/jobs/${item.taskId}`);
      if (res.status === 404) {
        if (item.kind === "doc") removeDocJob(item.target);
        else removeLibJob(item.target);
        continue;
      }
      if (!res.ok) continue;
      const data = await res.json();
      jobStatusMap.value[data.task_id] = data.status;
      if (data.status === "success") {
        const isIngest = data.job_type === "ingest_document";
        const msg =
          item.kind === "doc"
            ? isIngest
              ? `文档 #${item.target} 上传解析完成`
              : `文档 #${item.target} 标签/摘要已完成`
            : `库 #${item.target} 标签/摘要已完成`;
        pushNotice(msg, "success");
        if (item.kind === "doc") {
          removeDocJob(item.target);
          needDocRefresh = true;
        } else {
          removeLibJob(item.target);
          needLibRefresh = true;
        }
      } else if (data.status === "failed") {
        const isIngest = data.job_type === "ingest_document";
        const msg =
          item.kind === "doc"
            ? isIngest
              ? `文档 #${item.target} 上传解析失败：${data.message || ""}`
              : `文档 #${item.target} 标签/摘要失败：${data.message || ""}`
            : `库 #${item.target} 标签/摘要失败：${data.message || ""}`;
        pushNotice(msg, "error");
        if (item.kind === "doc") removeDocJob(item.target);
        else removeLibJob(item.target);
      }
    } catch (_) {
      continue;
    }
  }
  if (needDocRefresh) await fetchDocuments();
  if (needLibRefresh) await fetchLibraries();
}

onUnmounted(() => {
  stopJobPolling();
});
</script>

<style scoped>
.notice-stack {
  position: fixed;
  top: 16px;
  left: 50%;
  transform: translateX(-50%);
  display: grid;
  gap: 8px;
  width: min(560px, calc(100% - 32px));
  z-index: 400;
  pointer-events: none;
}
.notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border-radius: 10px;
  border: 1px solid #d9d9e3;
  background: #ffffff;
  color: #2a2a30;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.12);
  pointer-events: auto;
}
.notice.info {
  border-color: rgba(125, 196, 255, 0.5);
  background: #f0f6ff;
}
.notice.warn {
  border-color: rgba(255, 209, 102, 0.6);
  background: #fff7e3;
}
.notice.error {
  border-color: rgba(255, 99, 99, 0.6);
  background: #ffecec;
}
.notice.success {
  border-color: rgba(126, 213, 122, 0.6);
  background: #edf9f0;
}
.notice-text {
  flex: 1;
  font-size: 0.95rem;
}
.notice-close {
  border: 1px solid #d9d9e3;
  background: transparent;
  color: inherit;
  border-radius: 6px;
  padding: 4px 8px;
  cursor: pointer;
}
</style>
