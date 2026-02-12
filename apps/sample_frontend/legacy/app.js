const API_BASE = "http://localhost:8000/api";

const createConvBtn = document.getElementById("createConv");
const uploadBtn = document.getElementById("uploadBtn");
const askBtn = document.getElementById("askBtn");

const conversationIdInput = document.getElementById("conversationId");
const uploadResult = document.getElementById("uploadResult");
const answerBox = document.getElementById("answer");

createConvBtn.addEventListener("click", async () => {
  const res = await fetch(`${API_BASE}/conversations`, { method: "POST" });
  const data = await res.json();
  conversationIdInput.value = data.conversation_id;
});

uploadBtn.addEventListener("click", async () => {
  const fileInput = document.getElementById("pdfFile");
  if (!fileInput.files.length) {
    uploadResult.textContent = "Please select a PDF file.";
    return;
  }

  const formData = new FormData();
  formData.append("file", fileInput.files[0]);

  const res = await fetch(`${API_BASE}/documents/upload`, {
    method: "POST",
    body: formData,
  });

  const data = await res.json();
  uploadResult.textContent = JSON.stringify(data, null, 2);
});

askBtn.addEventListener("click", async () => {
  const question = document.getElementById("question").value.trim();
  const conversationId = conversationIdInput.value.trim();
  if (!conversationId) {
    answerBox.textContent = "Please create a conversation first.";
    return;
  }
  if (!question) {
    answerBox.textContent = "Please enter a question.";
    return;
  }

  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: Number(conversationId), question }),
  });

  const data = await res.json();
  answerBox.textContent = JSON.stringify(data, null, 2);
});
