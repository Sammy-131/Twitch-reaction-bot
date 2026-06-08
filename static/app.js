const statusMessage = document.getElementById("statusMessage");
const messages = document.getElementById("messages");
const backendUrlInput = document.getElementById("backendUrlInput");
const channelInput = document.getElementById("channelInput");
const keywordsInput = document.getElementById("keywordsInput");
const connectButton = document.getElementById("connectButton");

let socket;

function getBackendBaseUrl() {
  const configuredUrl = window.BACKEND_URL?.trim() || backendUrlInput?.value.trim();
  if (configuredUrl) {
    return configuredUrl.replace(/\/$/, "");
  }
  return `${window.location.protocol}//${window.location.host}`;
}

function getWebSocketUrl() {
  const baseUrl = getBackendBaseUrl();
  const protocol = baseUrl.startsWith("https") ? "wss" : "ws";
  return `${protocol}://${baseUrl.replace(/^https?:\/\//, "")}/ws`;
}

function getConfigUrl() {
  const baseUrl = getBackendBaseUrl();
  return `${baseUrl.replace(/\/$/, "")}/config`;
}

function addMessage(text, type = "info") {
  const messageElement = document.createElement("div");
  messageElement.className = `message ${type}`;
  messageElement.textContent = text;
  messages.prepend(messageElement);
}

function setStatus(text, isError = false) {
  statusMessage.textContent = text;
  statusMessage.className = isError ? "status error" : "status";
}

async function loadDefaults() {
  try {
    const response = await fetch(getConfigUrl());
    const config = await response.json();
    if (config.defaultChannel) {
      channelInput.value = config.defaultChannel;
    }
    if (config.defaultKeywords) {
      keywordsInput.value = config.defaultKeywords;
    }
  } catch (error) {
    console.warn("Unable to load config", error);
  }
}

function connectWebSocket() {
  if (socket && socket.readyState === WebSocket.OPEN) {
    addMessage("Already connected to WebSocket. Updating channel/keywords...");
    sendSettings();
    return;
  }

  socket = new WebSocket(getWebSocketUrl());

  socket.addEventListener("open", () => {
    setStatus("Connected to bot backend.");
    sendSettings();
  });

  socket.addEventListener("message", (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === "status") {
        setStatus(data.message);
        addMessage(data.message, "status");
      } else if (data.type === "chat") {
        addMessage(`[CHAT] #${data.channel} ${data.username}: ${data.message}`, "chat");
      } else if (data.type === "reaction") {
        addMessage(`[REACTION] ${data.reaction}`, "reaction");
      }
    } catch (error) {
      console.error("Invalid socket message", error);
    }
  });

  socket.addEventListener("close", () => {
    setStatus("WebSocket disconnected.", true);
    addMessage("WebSocket connection closed.", "error");
  });

  socket.addEventListener("error", (event) => {
    setStatus("WebSocket error.", true);
    addMessage("WebSocket error occurred.", "error");
  });
}

function sendSettings() {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  const payload = {
    type: "settings",
    channel: channelInput.value.trim(),
    keywords: keywordsInput.value.trim(),
  };
  socket.send(JSON.stringify(payload));
}

connectButton.addEventListener("click", () => {
  if (!channelInput.value.trim()) {
    setStatus("Please enter a streamer channel.", true);
    return;
  }
  connectWebSocket();
});

loadDefaults();
