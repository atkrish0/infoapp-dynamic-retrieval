// ai_widget.js
(() => {
  const AGENT_ENDPOINT = "/agent/dispatch";
  const TIMEOUT_MS = 20000;
  const MAX_MSGS = 200;

  if (window.__aiWidgetInjected) return;
  window.__aiWidgetInjected = true;

  const onReady = (fn) =>
    document.readyState === "loading"
      ? document.addEventListener("DOMContentLoaded", fn, { once: true })
      : fn();

  const escapeHTML = (s) =>
    String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

  const inferIntent = (q) => {
    const s = (q || "").toLowerCase();
    if (/\bsummar/i.test(s)) return "summarize";
    if (/\bexplain|\bwhy\b/i.test(s)) return "explain";
    if (/\bcompare|vs\.?|versus\b/i.test(s)) return "compare";
    if (/%|percent|increase|delta|avg|mean|sum|min|max|table\b/.test(s)) return "calc";
    return "general";
  };

  const getContext = () => ({ title: document.title, url: location.href });

  async function postJSON(url, body, timeoutMs) {
    const controller = new AbortController();
    const to = setTimeout(() => controller.abort(), timeoutMs || TIMEOUT_MS);
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } finally { clearTimeout(to); }
  }

  onReady(() => {
    const host = document.createElement("div");
    host.style.position = "fixed";
    host.style.right = "16px";
    host.style.bottom = "16px";
    host.style.zIndex = "2147483647";
    document.documentElement.appendChild(host);
    const shadow = host.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    style.textContent = `
      .btn{all:unset;cursor:pointer;padding:8px 12px;border-radius:999px;background:#fff;border:1px solid #d0d7de;
           font:14px system-ui,sans-serif;box-shadow:0 4px 12px rgba(0,0,0,.15)}
      .panel{display:none;position:fixed;right:0;bottom:48px;width:380px;max-height:70vh;overflow:auto;background:#fff;
             border:1px solid #d0d7de;border-radius:12px;padding:10px;box-shadow:0 10px 24px rgba(0,0,0,.18);
             font:14px system-ui,sans-serif}
      .open .panel{display:block}
      .row{display:flex;gap:8px;align-items:center;margin-top:8px}
      .input{flex:1;padding:6px 8px;border:1px solid #d0d7de;border-radius:8px}
      .msgs{max-height:50vh;overflow:auto}
      .msg{margin:6px 0}
      .spinner{display:inline-block;width:16px;height:16px;border:2px solid #bbb;border-top-color:#555;border-radius:50%;
               animation:spin .75s linear infinite;margin-left:6px;vertical-align:middle}
      @keyframes spin{to{transform:rotate(360deg)}}
      a{color:#0b57d0;text-decoration:none}
      a:hover{text-decoration:underline}
      @media print{ :host{ display:none !important } }
    `;
    shadow.appendChild(style);

    const root = document.createElement("div");
    root.innerHTML = `
      <button id="ai-toggle" class="btn">🤖 Ask AI</button>
      <div id="ai-panel" class="panel">
        <div id="ai-msgs" class="msgs"></div>
        <div class="row">
          <input id="ai-input" class="input" placeholder="Ask about this report…"/>
          <button id="ai-send" class="btn">Send</button>
        </div>
      </div>
    `;
    shadow.appendChild(root);

    const $ = (sel) => shadow.querySelector(sel);
    const toggleBtn = $("#ai-toggle");
    const panel = $("#ai-panel");
    const msgs = $("#ai-msgs");
    const input = $("#ai-input");
    const send = $("#ai-send");

    const docIdMeta = document.querySelector('meta[name="report-doc-id"]');
    const dsIdMeta  = document.querySelector('meta[name="report-dataset-id"]');
    const defaultDoc = (() => {
      const name = location.pathname.split("/").pop() || "";
      return name.endsWith(".html") ? name.slice(0, -5) + ".json" : undefined;
    })();
    const DOC_ID = docIdMeta?.content || defaultDoc;
    const DATASET_ID = dsIdMeta?.content || "Sheet_1";

    const addMsg = (role, html) => {
      const d = document.createElement("div");
      d.className = "msg";
      d.innerHTML = `<strong>${escapeHTML(role)}:</strong> ${html}`;
      msgs.appendChild(d);
      while (msgs.children.length > MAX_MSGS) msgs.removeChild(msgs.firstChild);
      msgs.scrollTop = msgs.scrollHeight;
      return d;
    };

    const setLoading = (node, on) => {
      if (!node) return;
      if (on) {
        const s = document.createElement("span");
        s.className = "spinner";
        s.dataset.spin = "1";
        node.appendChild(s);
      } else {
        node.querySelectorAll("[data-spin]").forEach((el) => el.remove());
      }
    };

    toggleBtn.addEventListener("click", () => {
      panel.parentElement.classList.toggle("open");
    });

    async function onSend() {
      const q = input.value.trim();
      if (!q) return;

      input.disabled = true; send.disabled = true;
      input.value = "";
      addMsg("You", escapeHTML(q));

      const payload = {
        query: q,
        intent: inferIntent(q),
        context: getContext(),
        doc_id: DOC_ID,
        dataset_id: DATASET_ID
      };

      console.log("[ai_widget] payload →", payload);
      const agentNode = addMsg("Agent", "Thinking…");
      setLoading(agentNode, true);

      try {
        const data = await postJSON(AGENT_ENDPOINT, payload, TIMEOUT_MS);
        setLoading(agentNode, false);
        agentNode.innerHTML = `<strong>Agent:</strong> ${escapeHTML(data.answer || "(no response)")}`;

        if (Array.isArray(data.citations) && data.citations.length) {
          const links = data.citations
            .map(c => `<a href="#${escapeHTML(c.anchorId || "")}">${escapeHTML(c.label || c.anchorId || "source")}</a>`)
            .join(" | ");
          addMsg("Agent", links);
        }
      } catch (e) {
        setLoading(agentNode, false);
        agentNode.innerHTML = `<strong>Agent:</strong> Error: ${escapeHTML(e.message || "request failed")}`;
      } finally {
        input.disabled = false; send.disabled = false; input.focus();
      }
    }

    send.addEventListener("click", onSend);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") onSend();
      if (e.key === "Escape") panel.parentElement.classList.remove("open");
    });
  });
})();
