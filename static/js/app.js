const STORAGE_KEY = "ai-chat-sessions";

let sessions = loadSessions();
let activeId = sessions[0]?.id ?? null;

const messagesEl = document.getElementById("messages");
const chatListEl = document.getElementById("chat-list");
const chatForm = document.getElementById("chat-form");
const userInput = document.getElementById("user-input");
const sendBtn = document.getElementById("send-btn");
const newChatBtn = document.getElementById("new-chat-btn");

function loadSessions() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
  } catch {
    return [];
  }
}

function saveSessions() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
}


function generateUUID() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // Fallback for non-secure contexts (plain HTTP)
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
    const r = Math.random() * 16 | 0;
    const v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

function createSession() {
  const session = {
    id: crypto.generateUUID(),
    title: "New chat",
    messages: [],
  };
  sessions.unshift(session);
  activeId = session.id;
  saveSessions();
  render();
  return session;
}

function getActiveSession() {
  return sessions.find((s) => s.id === activeId);
}

function renderChatList() {
  chatListEl.innerHTML = "";
  sessions.forEach((session) => {
    const item = document.createElement("div");
    item.className = "chat-item" + (session.id === activeId ? " active" : "");
    item.textContent = session.title;
    item.onclick = () => {
      activeId = session.id;
      render();
    };
    chatListEl.appendChild(item);
  });
}

function renderMessages() {
  const session = getActiveSession();
  messagesEl.innerHTML = "";

  if (!session || session.messages.length === 0) {
    messagesEl.innerHTML = `
      <div class="welcome">
        <h1>AI Chat</h1>
        <p>Ask anything to get started.</p>
      </div>`;
    return;
  }

  session.messages.forEach((msg) => {
    messagesEl.appendChild(createMessageEl(msg.role, msg.content));
  });

  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function createMessageEl(role, content, typing = false) {
  const row = document.createElement("div");
  row.className = `message-row ${role}` + (typing ? " typing" : "");

  const avatar = role === "user" ? "You" : "AI";
  row.innerHTML = `
    <div class="message">
      <div class="message-avatar">${avatar}</div>
      <div class="message-content">${escapeHtml(content)}</div>
    </div>`;
  return row;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function render() {
  renderChatList();
  renderMessages();
}

async function sendMessage(text) {
  let session = getActiveSession();
  if (!session) {
    session = createSession();
  }

  session.messages.push({ role: "user", content: text });
  if (session.title === "New chat") {
    session.title = text.slice(0, 40) + (text.length > 40 ? "…" : "");
  }
  saveSessions();
  render();

  const typingEl = createMessageEl("assistant", "", true);
  messagesEl.querySelector(".welcome")?.remove();
  messagesEl.appendChild(typingEl);
  messagesEl.scrollTop = messagesEl.scrollHeight;

  sendBtn.disabled = true;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: session.messages }),
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Request failed");
    }

    session.messages.push({ role: "assistant", content: data.message });
    saveSessions();
  } catch (err) {
    session.messages.push({
      role: "assistant",
      content: `Error: ${err.message}`,
    });
    saveSessions();
  } finally {
    sendBtn.disabled = false;
    renderMessages();
    userInput.focus();
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = userInput.value.trim();
  if (!text) return;
  userInput.value = "";
  userInput.style.height = "auto";
  sendMessage(text);
});

userInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatForm.requestSubmit();
  }
});

userInput.addEventListener("input", () => {
  userInput.style.height = "auto";
  userInput.style.height = Math.min(userInput.scrollHeight, 200) + "px";
});

newChatBtn.addEventListener("click", () => {
  createSession();
  userInput.focus();
});

render();
