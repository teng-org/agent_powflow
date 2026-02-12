<template>
  <div class="page doc-chunks-layout">
    <section class="card panel fade-up">
      <div class="panel-header">
        <div>
          <div class="section-title">知识库</div>
          <strong v-if="docDetail">
            文档 #{{ docDetail.id }} · {{ docDetail.filename }}
          </strong>
          <div v-if="docDetail" class="meta">
            chunks: {{ chunkCount }} · status:
            {{ docDetail.embedding_dirty ? "needs re-embed" : "ok" }}
          </div>
        </div>
        <div style="display: flex; gap: 10px; flex-wrap: wrap">
          <button class="btn ghost" @click="goBack">Back</button>
          <button class="btn soft" @click="refresh">Refresh</button>
          <button class="btn ghost" :disabled="!docId" @click="embedDocument">
            Embed
          </button>
          <button class="btn primary" :disabled="!docId" @click="reembedDocument">
            Re-embed
          </button>
          <button class="btn primary" :disabled="!docId" @click="loadMoreChunks">
            Load more
          </button>
        </div>
      </div>

      <div class="panel-header">
        <input v-model="chunkSearch" placeholder="Search chunks" />
        <select v-model="chunkSort">
          <option value="page_asc">Page asc</option>
          <option value="page_desc">Page desc</option>
          <option value="id_asc">ID asc</option>
          <option value="id_desc">ID desc</option>
          <option value="status_dirty">Needs re-embed</option>
        </select>
      </div>

      <div class="chunk-grid chunk-grid-wide">
        <div v-if="!filteredChunks.length" class="list-item">
          No chunks
        </div>
        <div
          v-for="chunk in filteredChunks"
          :key="chunk.id"
          class="chunk-card"
          @click="openChunk(chunk)"
        >
          <div>
            <strong>#{{ chunk.id }}</strong> | page: {{ chunk.meta?.page || "-" }}
          </div>
          <div class="meta">
            status: {{ chunk.embedding_dirty ? "needs re-embed" : "ok" }}
          </div>
          <div class="meta">tag: {{ chunk.tag || "-" }}</div>
          <div class="meta">summary: {{ chunk.summary || "-" }}</div>
          <pre>{{ chunk.content?.slice(0, 400) }}</pre>
        </div>
      </div>
    </section>

    <div v-if="modalOpen" class="modal" @click.self="closeModal">
      <div class="modal-card">
        <div class="panel-header">
          <strong>Edit chunk #{{ modalChunk?.id }}</strong>
          <button class="btn ghost" @click="closeModal">Close</button>
        </div>
        <label class="field">
          Content
          <textarea v-model="modalContent" rows="8"></textarea>
        </label>
        <div style="display: flex; gap: 10px">
          <button class="btn ghost" @click="autoTagChunk">Auto Tag</button>
          <button class="btn primary" @click="saveChunk">Save</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { API_BASE } from "../lib/api";
import { adminFetch } from "../lib/auth";

const route = useRoute();
const router = useRouter();

const docId = ref(Number(route.params.id) || null);
const docDetail = ref(null);
const chunks = ref([]);
const chunkCount = ref(0);

const chunkSearch = ref("");
const chunkSort = ref("page_asc");
const chunkLimit = ref(50);

const modalOpen = ref(false);
const modalChunk = ref(null);
const modalContent = ref("");

onMounted(async () => {
  await loadData();
});

watch(
  () => route.params.id,
  async (next) => {
    docId.value = Number(next) || null;
    chunkLimit.value = 50;
    await loadData();
  }
);

const filteredChunks = computed(() => {
  const search = chunkSearch.value.trim().toLowerCase();
  let list = chunks.value.filter((chunk) => {
    if (!search) return true;
    const target = `${chunk.tag || ""} ${chunk.summary || ""} ${
      chunk.content || ""
    }`.toLowerCase();
    return target.includes(search);
  });
  list = list.slice().sort((a, b) => sortChunk(a, b, chunkSort.value));
  return list;
});

async function loadData() {
  if (!docId.value) return;
  await fetchDocumentDetail();
  await fetchChunks();
}

async function fetchDocumentDetail() {
  const res = await adminFetch(`${API_BASE}/documents/${docId.value}`);
  const data = await res.json();
  docDetail.value = data.document;
  chunkCount.value = data.chunk_count || 0;
}

async function fetchChunks() {
  if (!docId.value) return;
  const res = await adminFetch(
    `${API_BASE}/chunks?document_id=${docId.value}&limit=${chunkLimit.value}`
  );
  const data = await res.json();
  chunks.value = data.chunks || [];
}

function sortChunk(a, b, sort) {
  if (sort === "id_asc") return a.id - b.id;
  if (sort === "id_desc") return b.id - a.id;
  if (sort === "page_asc") return (a.meta?.page || 0) - (b.meta?.page || 0);
  if (sort === "page_desc") return (b.meta?.page || 0) - (a.meta?.page || 0);
  if (sort === "status_dirty") {
    return (b.embedding_dirty ? 1 : 0) - (a.embedding_dirty ? 1 : 0);
  }
  return 0;
}

function openChunk(chunk) {
  modalChunk.value = chunk;
  modalContent.value = chunk.content || "";
  modalOpen.value = true;
}

function closeModal() {
  modalOpen.value = false;
  modalChunk.value = null;
}

async function saveChunk() {
  if (!modalChunk.value) return;
  const res = await adminFetch(`${API_BASE}/chunks/${modalChunk.value.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: modalContent.value }),
  });
  const updated = await res.json();
  const idx = chunks.value.findIndex((item) => item.id === updated.id);
  if (idx >= 0) {
    chunks.value[idx] = updated;
  }
  closeModal();
  await fetchDocumentDetail();
}

async function autoTagChunk() {
  if (!modalChunk.value) return;
  const res = await adminFetch(
    `${API_BASE}/chunks/${modalChunk.value.id}/auto-meta`,
    { method: "POST" }
  );
  const updated = await res.json();
  const idx = chunks.value.findIndex((item) => item.id === updated.id);
  if (idx >= 0) {
    chunks.value[idx] = updated;
  }
  await fetchDocumentDetail();
}

async function loadMoreChunks() {
  if (!docId.value) return;
  chunkLimit.value += 50;
  await fetchChunks();
}

async function refresh() {
  chunkLimit.value = 50;
  await loadData();
}

async function embedDocument() {
  if (!docId.value) return;
  await adminFetch(`${API_BASE}/documents/${docId.value}/embed`, { method: "POST" });
  await fetchDocumentDetail();
  await fetchChunks();
}

async function reembedDocument() {
  if (!docId.value) return;
  await adminFetch(`${API_BASE}/documents/${docId.value}/reembed`, { method: "POST" });
  await fetchDocumentDetail();
  await fetchChunks();
}

function goBack() {
  router.push({ name: "knowledge" });
}
</script>
