const API_BASE =
  (typeof VITE_API_BASE !== "undefined" && VITE_API_BASE) ||
  `${window.location.origin}/api`;

const docTitleEl = document.getElementById("docTitle");
const docDetailEl = document.getElementById("docDetail");
const docMetaEl = document.getElementById("docMeta");
const chunkGridEl = document.getElementById("chunkGrid");
const loadMoreBtn = document.getElementById("loadMoreBtn");
const backBtn = document.getElementById("backBtn");
const reembedDocBtn = document.getElementById("reembedDocBtn");
const chunkSearchInput = document.getElementById("chunkSearch");
const chunkSortSelect = document.getElementById("chunkSort");

const modal = document.getElementById("modal");
const modalTitle = document.getElementById("modalTitle");
const modalContent = document.getElementById("modalContent");
const modalAutoTag = document.getElementById("modalAutoTag");
const modalSave = document.getElementById("modalSave");
const modalCancel = document.getElementById("modalCancel");

let chunks = [];
let chunkLimit = 50;
let modalChunkId = null;
let activeChunkId = null;

const urlParams = new URLSearchParams(window.location.search);
const documentId = Number(urlParams.get("document_id"));

if (!documentId) {
  docTitleEl.textContent = "Missing document_id";
}

async function loadDocumentDetail() {
  if (!documentId) return;
  const res = await fetch(`${API_BASE}/documents/${documentId}`);
  const data = await res.json();
  const doc = data.document;
  docTitleEl.textContent = doc.filename;
  docMetaEl.textContent = `document #${doc.id} | chunks: ${data.chunk_count}`;
  docDetailEl.innerHTML = `
    <div class="doc-title">${doc.filename}</div>
    <div class="doc-meta">status: ${doc.embedding_dirty ? "needs re-embed" : "ok"}</div>
    <div class="doc-meta">tag: ${doc.tag || "-"}</div>
    <div class="doc-meta">summary: ${doc.summary || "-"}</div>
  `;
  if (doc.embedding_dirty) {
    reembedDocBtn.disabled = false;
  } else {
    reembedDocBtn.disabled = true;
  }
}

async function loadChunks() {
  if (!documentId) return;
  const res = await fetch(
    `${API_BASE}/chunks?document_id=${documentId}&limit=${chunkLimit}`
  );
  const data = await res.json();
  chunks = data.chunks || [];
  renderChunks();
}

function renderChunks() {
  chunkGridEl.innerHTML = "";
  const search = chunkSearchInput.value.trim().toLowerCase();
  const sort = chunkSortSelect.value;

  let list = chunks.filter((chunk) => {
    if (!search) return true;
    const target = `${chunk.tag || ""} ${chunk.summary || ""} ${chunk.content || ""}`.toLowerCase();
    return target.includes(search);
  });

  list = list.slice().sort((a, b) => {
    if (sort === "id_asc") return a.id - b.id;
    if (sort === "id_desc") return b.id - a.id;
    if (sort === "page_asc") return (a.meta?.page || 0) - (b.meta?.page || 0);
    if (sort === "page_desc") return (b.meta?.page || 0) - (a.meta?.page || 0);
    if (sort === "status_dirty") {
      return (b.embedding_dirty ? 1 : 0) - (a.embedding_dirty ? 1 : 0);
    }
    return 0;
  });

  if (!list.length) {
    chunkGridEl.innerHTML = "<div class=\"doc-item\">No chunks</div>";
    return;
  }

  list.forEach((chunk) => {
    const meta = chunk.meta || {};
    const item = document.createElement("div");
    item.className = "chunk-card" + (chunk.id === activeChunkId ? " active" : "");
    item.innerHTML = `
      <div><strong>Chunk #${chunk.id} (C${chunk.local_index || "-"})</strong> | page: ${meta.page || "-"}</div>
      <div class="doc-meta">status: ${chunk.embedding_dirty ? "needs re-embed" : "ok"}</div>
      <div class="doc-meta">tag: ${chunk.tag || "-"}</div>
      <div class="doc-meta">summary: ${chunk.summary || "-"}</div>
      <pre>${chunk.content.slice(0, 300)}</pre>
    `;
    item.addEventListener("click", () => openModal(chunk.id));
    chunkGridEl.appendChild(item);
  });
}

function openModal(chunkId) {
  const chunk = chunks.find((item) => item.id === chunkId);
  if (!chunk) return;
  activeChunkId = chunkId;
  renderChunks();
  modalChunkId = chunkId;
  modalTitle.textContent = `Edit chunk #${chunkId}`;
  modalContent.value = chunk.content || "";
  modal.hidden = false;
}

function closeModal() {
  modal.hidden = true;
  modalChunkId = null;
}

async function saveModal() {
  if (!modalChunkId) return;
  const payload = {
    content: modalContent.value,
  };
  const res = await fetch(`${API_BASE}/chunks/${modalChunkId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const updated = await res.json();
  const idx = chunks.findIndex((item) => item.id === modalChunkId);
  if (idx >= 0) {
    chunks[idx] = updated;
  }
  closeModal();
  renderChunks();
  await loadDocumentDetail();
}

async function autoTagChunk() {
  if (!modalChunkId) return;
  modalAutoTag.disabled = true;
  modalAutoTag.classList.add("btn-loading");
  modalAutoTag.textContent = "Auto Tag...";
  const res = await fetch(`${API_BASE}/chunks/${modalChunkId}/auto-meta`, {
    method: "POST",
  });
  const updated = await res.json();
  const idx = chunks.findIndex((item) => item.id === modalChunkId);
  if (idx >= 0) {
    chunks[idx] = updated;
  }
  modalAutoTag.classList.remove("btn-loading");
  modalAutoTag.textContent = "Auto Tag";
  modalAutoTag.disabled = false;
  renderChunks();
  await loadDocumentDetail();
}

async function reembedDocument() {
  if (!documentId) return;
  if (reembedDocBtn.disabled) {
    alert("No changes detected. Re-embed not required.");
    return;
  }
  reembedDocBtn.disabled = true;
  reembedDocBtn.classList.add("btn-loading");
  reembedDocBtn.textContent = "Re-embedding...";
  await fetch(`${API_BASE}/documents/${documentId}/reembed`, { method: "POST" });
  reembedDocBtn.classList.remove("btn-loading");
  reembedDocBtn.textContent = "Re-embed";
  await loadDocumentDetail();
  await loadChunks();
}

function goBack() {
  window.location.href = "console.html";
}

loadMoreBtn.addEventListener("click", () => {
  chunkLimit += 50;
  loadChunks();
});
backBtn.addEventListener("click", goBack);
reembedDocBtn.addEventListener("click", reembedDocument);
chunkSearchInput.addEventListener("input", renderChunks);
chunkSortSelect.addEventListener("change", renderChunks);

modalSave.addEventListener("click", saveModal);
modalAutoTag.addEventListener("click", autoTagChunk);
modalCancel.addEventListener("click", closeModal);
// Modal can only be closed via Save or Cancel.

loadDocumentDetail();
loadChunks();
