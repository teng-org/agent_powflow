const API_BASE =
  (typeof VITE_API_BASE !== "undefined" && VITE_API_BASE) ||
  `${window.location.origin}/api`;

const docListEl = document.getElementById("docList");
const filterInput = document.getElementById("filterInput");
const refreshBtn = document.getElementById("refreshBtn");
const libraryListEl = document.getElementById("libraryList");
const createLibraryBtn = document.getElementById("createLibraryBtn");
const librarySearchInput = document.getElementById("librarySearch");
const librarySortSelect = document.getElementById("librarySort");
const documentSortSelect = document.getElementById("documentSort");
const uploadBtn = document.getElementById("uploadBtn");
const uploadFileInput = document.getElementById("uploadFile");
const uploadLibrarySelect = document.getElementById("uploadLibrarySelect");

let libraries = [];
let documents = [];
let activeLibraryId = null;
let activeDocumentId = null;
const ACTIVE_LIBRARY_KEY = "activeLibraryId";

const storedLibraryId = Number(localStorage.getItem(ACTIVE_LIBRARY_KEY));
if (!Number.isNaN(storedLibraryId)) {
  activeLibraryId = storedLibraryId;
}

async function fetchLibraries() {
  const res = await fetch(`${API_BASE}/libraries`);
  const data = await res.json();
  libraries = data.libraries || [];
  if (activeLibraryId !== null && !libraries.some((lib) => lib.id === activeLibraryId)) {
    activeLibraryId = null;
    localStorage.removeItem(ACTIVE_LIBRARY_KEY);
  }
  renderLibraries();
  renderUploadLibraries();
}

async function fetchDocuments() {
  const url = activeLibraryId
    ? `${API_BASE}/documents?library_id=${activeLibraryId}`
    : `${API_BASE}/documents`;
  const res = await fetch(url);
  const data = await res.json();
  documents = data.documents || [];
  renderDocuments();
}

function renderLibraries() {
  libraryListEl.innerHTML = "";
  const search = librarySearchInput.value.trim().toLowerCase();
  const sort = librarySortSelect.value;

  const allItem = document.createElement("div");
  allItem.className = "doc-item" + (activeLibraryId === null ? " active" : "");
  allItem.textContent = "All Libraries";
  allItem.addEventListener("click", () => setActiveLibrary(null));
  libraryListEl.appendChild(allItem);

  let list = libraries.filter((lib) => {
    if (!search) return true;
    const target = `${lib.name} ${lib.tag || ""}`.toLowerCase();
    return target.includes(search);
  });

  list = list.slice().sort((a, b) => {
    if (sort === "id_asc") return a.id - b.id;
    if (sort === "id_desc") return b.id - a.id;
    if (sort === "name_asc") return a.name.localeCompare(b.name);
    if (sort === "name_desc") return b.name.localeCompare(a.name);
    return 0;
  });

  list.forEach((lib) => {
    const item = document.createElement("div");
    item.className = "doc-item" + (lib.id === activeLibraryId ? " active" : "");
    item.innerHTML = `
      <div class="doc-title">#${lib.id} ${lib.name}</div>
      <div class="doc-meta">tag: ${lib.tag || "-"}</div>
      <div class="doc-meta">summary: ${lib.summary || "-"}</div>
      <div class="row-actions">
        <button data-auto-library="${lib.id}">Auto Tag</button>
        <button data-delete-library="${lib.id}">Delete</button>
      </div>
    `;
    item.addEventListener("click", () => setActiveLibrary(lib.id));
    libraryListEl.appendChild(item);
  });

  document.querySelectorAll("[data-delete-library]").forEach((btn) => {
    btn.addEventListener("click", async (event) => {
      event.stopPropagation();
      const libId = btn.getAttribute("data-delete-library");
      const ok = confirm("Delete this library and all its documents and embeddings?");
      if (!ok) return;
      await fetch(`${API_BASE}/libraries/${libId}`, { method: "DELETE" });
      if (activeLibraryId === Number(libId)) {
        activeLibraryId = null;
        localStorage.removeItem(ACTIVE_LIBRARY_KEY);
      }
      await fetchLibraries();
      await fetchDocuments();
    });
  });

  document.querySelectorAll("[data-auto-library]").forEach((btn) => {
    btn.addEventListener("click", async (event) => {
      event.stopPropagation();
      const libId = btn.getAttribute("data-auto-library");
      btn.disabled = true;
      btn.classList.add("btn-loading");
      btn.textContent = "Auto Tag...";
      await fetch(`${API_BASE}/libraries/${libId}/auto-meta`, { method: "POST" });
      btn.classList.remove("btn-loading");
      btn.textContent = "Auto Tag";
      btn.disabled = false;
      await fetchLibraries();
    });
  });
}

function renderUploadLibraries() {
  uploadLibrarySelect.innerHTML = '<option value="">Select library</option>';
  libraries.forEach((lib) => {
    const option = document.createElement("option");
    option.value = String(lib.id);
    option.textContent = `${lib.name} (#${lib.id})`;
    uploadLibrarySelect.appendChild(option);
  });
}

function renderLibraryOptions(selectedId) {
  return libraries
    .map((lib) => {
      const selected = lib.id === selectedId ? "selected" : "";
      return `<option value="${lib.id}" ${selected}>${lib.name} (#${lib.id})</option>`;
    })
    .join("");
}

function renderDocuments() {
  const filter = filterInput.value.trim().toLowerCase();
  const sort = documentSortSelect.value;
  docListEl.innerHTML = "";

  let filtered = documents.filter((doc) => {
    if (!filter) return true;
    const target = `${doc.filename} ${doc.tag || ""}`.toLowerCase();
    return target.includes(filter);
  });

  filtered = filtered.slice().sort((a, b) => {
    if (sort === "id_asc") return a.id - b.id;
    if (sort === "id_desc") return b.id - a.id;
    if (sort === "name_asc") return a.filename.localeCompare(b.filename);
    if (sort === "name_desc") return b.filename.localeCompare(a.filename);
    if (sort === "status_dirty") {
      return (b.embedding_dirty ? 1 : 0) - (a.embedding_dirty ? 1 : 0);
    }
    return 0;
  });

  if (!filtered.length) {
    docListEl.innerHTML = "<div class=\"doc-item\">No documents</div>";
    return;
  }

  filtered.forEach((doc) => {
    const item = document.createElement("div");
    item.className = "doc-item" + (doc.id === activeDocumentId ? " active" : "");
    const hasChunks = doc.chunk_count > 0;
    const hasEmbedded = doc.embedded_count > 0;
    item.innerHTML = `
      <div class="doc-title">#${doc.id} (L${doc.local_index || "-"}) ${doc.filename}</div>
      <div class="badge-row">
        <span class="badge ${doc.embedding_dirty ? "badge-warn" : "badge-ok"}">
          ${doc.embedding_dirty ? "needs re-embed" : "ok"}
        </span>
        <span class="badge badge-info">chunks ${doc.chunk_count}</span>
        <span class="badge badge-info">embedded ${doc.embedded_count}</span>
      </div>
      <div class="doc-meta">tag: ${doc.tag || "-"}</div>
      <div class="doc-meta">summary: ${doc.summary || "-"}</div>
      <div class="row-actions">
        <select class="select-inline" data-doc-library="${doc.id}">
          ${renderLibraryOptions(doc.library_id)}
        </select>
        <button data-update-library="${doc.id}">Move</button>
      </div>
      <div class="row-actions">
        <button data-auto-doc="${doc.id}" ${hasChunks ? "" : "disabled"}>Auto Tag</button>
        <button data-chunk-doc="${doc.id}" ${hasChunks ? "disabled" : ""}>Chunk</button>
        <button data-embed-doc="${doc.id}" ${hasChunks && !hasEmbedded ? "" : "disabled"}>Embed</button>
        <button data-delete-chunks="${doc.id}">Delete chunks</button>
        <button data-delete-doc="${doc.id}">Delete doc</button>
      </div>
    `;
    item.addEventListener("click", () => {
      activeDocumentId = doc.id;
      renderDocuments();
      window.location.href = `chunks.html?document_id=${doc.id}`;
    });
    docListEl.appendChild(item);
  });

  document.querySelectorAll("[data-chunk-doc]").forEach((btn) => {
    btn.addEventListener("click", async (event) => {
      event.stopPropagation();
      const docId = btn.getAttribute("data-chunk-doc");
      if (btn.disabled) {
        alert("Chunks already exist. Delete chunks to re-chunk.");
        return;
      }
      btn.disabled = true;
      btn.classList.add("btn-loading");
      btn.textContent = "Chunking...";
      await fetch(`${API_BASE}/documents/${docId}/chunk`, { method: "POST" });
      btn.classList.remove("btn-loading");
      btn.textContent = "Chunk";
      btn.disabled = false;
      await fetchDocuments();
    });
  });

  document.querySelectorAll("[data-embed-doc]").forEach((btn) => {
    btn.addEventListener("click", async (event) => {
      event.stopPropagation();
      const docId = btn.getAttribute("data-embed-doc");
      if (btn.disabled) {
        alert("Please chunk first, or delete embeddings to re-embed.");
        return;
      }
      btn.disabled = true;
      btn.classList.add("btn-loading");
      btn.textContent = "Embedding...";
      await fetch(`${API_BASE}/documents/${docId}/embed`, { method: "POST" });
      btn.classList.remove("btn-loading");
      btn.textContent = "Embed";
      btn.disabled = false;
      await fetchDocuments();
    });
  });

  document.querySelectorAll("[data-delete-chunks]").forEach((btn) => {
    btn.addEventListener("click", async (event) => {
      event.stopPropagation();
      const docId = btn.getAttribute("data-delete-chunks");
      const ok = confirm("Delete all chunks and embeddings for this document?");
      if (!ok) return;
      await fetch(`${API_BASE}/documents/${docId}/chunks`, { method: "DELETE" });
      await fetchDocuments();
    });
  });

  document.querySelectorAll("[data-delete-doc]").forEach((btn) => {
    btn.addEventListener("click", async (event) => {
      event.stopPropagation();
      const docId = btn.getAttribute("data-delete-doc");
      const ok = confirm("Delete this document and all its chunks?");
      if (!ok) return;
      await fetch(`${API_BASE}/documents/${docId}`, { method: "DELETE" });
      await fetchDocuments();
    });
  });

  document.querySelectorAll("[data-doc-library]").forEach((selectEl) => {
    selectEl.addEventListener("click", (event) => event.stopPropagation());
  });

  document.querySelectorAll("[data-update-library]").forEach((btn) => {
    btn.addEventListener("click", async (event) => {
      event.stopPropagation();
      const docId = btn.getAttribute("data-update-library");
      const selectEl = document.querySelector(`[data-doc-library="${docId}"]`);
      const libraryId = Number(selectEl.value);
      await fetch(`${API_BASE}/documents/${docId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ library_id: libraryId }),
      });
      await fetchDocuments();
    });
  });

  document.querySelectorAll("[data-auto-doc]").forEach((btn) => {
    btn.addEventListener("click", async (event) => {
      event.stopPropagation();
      const docId = btn.getAttribute("data-auto-doc");
      if (btn.disabled) {
        alert("Please chunk the document before auto tagging.");
        return;
      }
      btn.disabled = true;
      btn.classList.add("btn-loading");
      btn.textContent = "Auto Tag...";
      const res = await fetch(`${API_BASE}/documents/${docId}/auto-meta`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        alert(data.detail || "Auto tag failed.");
      }
      btn.classList.remove("btn-loading");
      btn.textContent = "Auto Tag";
      btn.disabled = false;
      await fetchDocuments();
    });
  });
}

async function setActiveLibrary(libraryId) {
  activeLibraryId = libraryId;
  if (libraryId === null) {
    localStorage.removeItem(ACTIVE_LIBRARY_KEY);
  } else {
    localStorage.setItem(ACTIVE_LIBRARY_KEY, String(libraryId));
  }
  activeDocumentId = null;
  renderLibraries();
  await fetchDocuments();
}

filterInput.addEventListener("input", renderDocuments);
librarySearchInput.addEventListener("input", renderLibraries);
librarySortSelect.addEventListener("change", renderLibraries);
documentSortSelect.addEventListener("change", renderDocuments);
refreshBtn.addEventListener("click", async () => {
  await fetchLibraries();
  await fetchDocuments();
});

createLibraryBtn.addEventListener("click", async () => {
  const name = prompt("Library name?");
  if (!name) return;
  const res = await fetch(`${API_BASE}/libraries`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  await res.json();
  await fetchLibraries();
});

uploadBtn.addEventListener("click", async () => {
  const file = uploadFileInput.files[0];
  if (!file) return;
  const libraryId = uploadLibrarySelect.value;
  if (!libraryId) {
    alert("Please select a library for this document.");
    return;
  }
  const formData = new FormData();
  formData.append("file", file);
  formData.append("library_id", libraryId);
  const res = await fetch(`${API_BASE}/documents/upload`, {
    method: "POST",
    body: formData,
  });
  await res.json();
  uploadFileInput.value = "";
  await fetchDocuments();
});

fetchLibraries();
fetchDocuments();
