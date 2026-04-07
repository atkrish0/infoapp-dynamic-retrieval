(() => {
  const AGENT_ENDPOINT = "/agent/dispatch";
  const TIMEOUT_MS = 30000;

  if (window.__creditcardWidgetInjected) return;
  window.__creditcardWidgetInjected = true;

  const onReady = (fn) =>
    document.readyState === "loading"
      ? document.addEventListener("DOMContentLoaded", fn, { once: true })
      : fn();

  const escapeHTML = (s) =>
    String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

  async function postJSON(url, body, timeoutMs) {
    const controller = new AbortController();
    const to = setTimeout(() => controller.abort(), timeoutMs || TIMEOUT_MS);
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!res.ok) {
        let detail = "";
        try {
          const text = await res.text();
          detail = text ? ` ${text}` : "";
        } catch (_) {}
        throw new Error(`HTTP ${res.status}${detail}`);
      }
      return await res.json();
    } finally {
      clearTimeout(to);
    }
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
      .panel{display:none;position:fixed;right:0;bottom:48px;width:420px;max-height:72vh;overflow:auto;background:#fff;
             border:1px solid #d0d7de;border-radius:12px;padding:10px;box-shadow:0 10px 24px rgba(0,0,0,.18);
             font:14px system-ui,sans-serif}
      .open .panel{display:block}
      .row{display:flex;gap:8px;align-items:center;margin-top:8px}
      .input{flex:1;padding:6px 8px;border:1px solid #d0d7de;border-radius:8px}
      .msgs{max-height:52vh;overflow:auto}
      .msg{margin:6px 0;line-height:1.35}
      .meta{opacity:.72;font-size:12px}
      .spinner{display:inline-block;width:16px;height:16px;border:2px solid #bbb;border-top-color:#555;border-radius:50%;
               animation:spin .75s linear infinite;margin-left:6px;vertical-align:middle}
      @keyframes spin{to{transform:rotate(360deg)}}
      ul{padding-left:18px}
      li{margin:2px 0}
      @media print{ :host{ display:none !important } }
    `;
    shadow.appendChild(style);

    const root = document.createElement("div");
    root.innerHTML = `
      <button id="ai-toggle" class="btn">Ask Data</button>
      <div id="ai-panel" class="panel">
        <div id="ai-msgs" class="msgs"></div>
        <div class="row">
          <input id="ai-input" class="input" placeholder="Ask about this credit-card report..."/>
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
    const dsIdMeta = document.querySelector('meta[name="report-dataset-id"]');
    const DOC_ID = docIdMeta?.content || "creditcard.xlsx";
    const DATASET_ID = dsIdMeta?.content || "Sheet_1";

    const addMsg = (role, html) => {
      const d = document.createElement("div");
      d.className = "msg";
      d.innerHTML = `<strong>${escapeHTML(role)}:</strong> ${html}`;
      msgs.appendChild(d);
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

      input.disabled = true;
      send.disabled = true;
      input.value = "";
      addMsg("You", escapeHTML(q));

      const payload = {
        query: q,
        context: { title: document.title, url: location.href },
        doc_id: DOC_ID,
        dataset_id: DATASET_ID,
      };

      const agentNode = addMsg("Agent", "Thinking...");
      setLoading(agentNode, true);

      try {
        const data = await postJSON(AGENT_ENDPOINT, payload, TIMEOUT_MS);
        setLoading(agentNode, false);

        const mode = data.mode ? `<div class="meta">mode=${escapeHTML(data.mode)}</div>` : "";
        agentNode.innerHTML = `<strong>Agent:</strong> ${mode}${escapeHTML(data.answer || "(no response)")}`;

        if (Array.isArray(data.citations) && data.citations.length) {
          const list = data.citations
            .map((c) => `<li>${escapeHTML(c.label || c.anchorId || "source")}</li>`)
            .join("");
          addMsg("Citations", `<ul>${list}</ul>`);
        }
      } catch (e) {
        setLoading(agentNode, false);
        agentNode.innerHTML = `<strong>Agent:</strong> Error: ${escapeHTML(e.message || "request failed")}`;
      } finally {
        input.disabled = false;
        send.disabled = false;
        input.focus();
      }
    }

    send.addEventListener("click", onSend);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") onSend();
      if (e.key === "Escape") panel.parentElement.classList.remove("open");
    });
  });
})();
