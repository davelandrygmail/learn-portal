/*
 * teach-chat.js — in-lesson /teach chat pane for the Learn Portal.
 *
 * A toggle button in the lesson navbar ("💬 Ask") opens a resizable lower-half
 * pane with a minimal chat UI that talks to the portal's /chat/{topic}
 * WebSocket. The WebSocket is only opened when the pane is shown, and closed
 * when it is hidden, so a merely-viewed lesson never spawns a Hermes session.
 *
 * Server-injected markup (see app.py _wrap_lesson_html):
 *   <button id="lp-teach-btn">💬 Ask</button>          (in navbar)
 *   #lp-chat[data-topic="..."] + <script defer src=/static/teach-chat.js>
 */
(function () {
  "use strict";

  var CHAT = document.getElementById("lp-chat");
  if (!CHAT) return;

  var topic = CHAT.getAttribute("data-topic") || "";
  var log = document.getElementById("lp-chat-log");
  var input = document.getElementById("lp-chat-msg");
  var sendBtn = document.getElementById("lp-chat-send");
  var statusEl = document.getElementById("lp-chat-status");
  var closeBtn = document.getElementById("lp-chat-close");
  var toggleBtn = document.getElementById("lp-teach-btn");

  var ws = null;
  var busy = false;
  var open = false;

  /* ---- tiny markdown-ish renderer (safe subset) ---- */
  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;")
            .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function renderMd(s) {
    var parts = s.split(/```/g);
    var html = "";
    for (var i = 0; i < parts.length; i++) {
      if (i % 2 === 1) {
        html += "<pre>" + esc(parts[i].replace(/^\w+\n/, "")) + "</pre>";
      } else {
        html += esc(parts[i])
          .replace(/^\s*[-*] /gm, "• ")
          .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
          .replace(/`([^`]+)`/g, "<code>$1</code>")
          .replace(/\n/g, "<br>");
      }
    }
    return html;
  }

  function addMsg(role, text) {
    var d = document.createElement("div");
    d.className = "lp-chat-" + role;
    var b = document.createElement("div");
    b.className = "lp-chat-bubble";
    b.innerHTML = renderMd(text);
    d.appendChild(b);
    log.appendChild(d);
    log.scrollTop = log.scrollHeight;
    return b;
  }

  function setStatus(t, loading) {
    if (statusEl) {
      statusEl.textContent = t || "";
      statusEl.classList.toggle("lp-chat-loading", !!loading);
    }
    if (toggleBtn) toggleBtn.classList.toggle("lp-chat-busy", !!loading);
  }

  function setBusy(b) {
    busy = b;
    if (input) input.disabled = b || !ws || ws.readyState !== WebSocket.OPEN;
    if (sendBtn) sendBtn.disabled = b || !ws || ws.readyState !== WebSocket.OPEN;
  }

  function connect() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    var proto = location.protocol === "https:" ? "wss://" : "ws://";
    ws = new WebSocket(proto + location.host + "/chat/" + encodeURIComponent(topic));
    ws.onopen = function () {
      setStatus("Ready — ask about this lesson");
      setBusy(false);
    };
    ws.onclose = function () {
      setStatus("Disconnected");
      setBusy(false);
      ws = null;
    };
    ws.onerror = function () {
      setStatus("Connection error");
    };
    ws.onmessage = function (evt) {
      var data;
      try { data = JSON.parse(evt.data); } catch (e) { return; }
      if (data.status === "thinking") {
        setStatus("Thinking…", true);
      } else if (data.delta) {
        setStatus("");
        addMsg("assistant", data.delta);
        setBusy(false);
        if (input) input.focus();
      } else if (data.error) {
        setStatus("");
        addMsg("assistant", "⚠️ " + data.error);
        setBusy(false);
      }
    };
  }

  function closePane() {
    if (ws) { try { ws.close(); } catch (e) {} ws = null; }
    CHAT.classList.remove("lp-chat-open");
    if (CHAT.setAttribute) CHAT.setAttribute("aria-hidden", "true");
    if (toggleBtn) toggleBtn.setAttribute("aria-pressed", "false");
    document.body.classList.remove("lp-chat-open");
    open = false;
  }

  function openPane() {
    document.body.classList.add("lp-chat-open");
    if (toggleBtn) toggleBtn.setAttribute("aria-pressed", "true");
    open = true;
    connect();
    if (input) input.focus();
  }

  function toggle() { open ? closePane() : openPane(); }

  function send() {
    var text = input ? input.value.trim() : "";
    if (!text || busy || !ws || ws.readyState !== WebSocket.OPEN) return;
    if (input) input.value = "";
    addMsg("user", text);
    setBusy(true);
    setStatus("Thinking…", true);
    ws.send(JSON.stringify({ message: text }));
  }

  /* ---- wire up UI ---- */
  if (sendBtn) sendBtn.addEventListener("click", send);
  if (input) input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
  if (toggleBtn) toggleBtn.addEventListener("click", toggle);
  if (closeBtn) closeBtn.addEventListener("click", closePane);

  // Pane starts closed; don't connect yet — only when opened.
})();
