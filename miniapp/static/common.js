/* ── Auth state ────────────────────────────────────────────────────────────── */
let _token = localStorage.getItem('fin_token') || null;
let _user  = JSON.parse(localStorage.getItem('fin_user') || 'null');
let _authPromise = null;

/* ── Utilities ─────────────────────────────────────────────────────────────── */
const $ = (id) => document.getElementById(id);

function money(v) {
  return Number(v || 0).toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function timeAgo(isoOrSqlite) {
  const str = (isoOrSqlite || '').replace(' ', 'T');
  const d = new Date(str + (str.includes('+') || str.endsWith('Z') ? '' : 'Z'));
  if (isNaN(d.getTime())) return isoOrSqlite;
  const diff = Math.floor((Date.now() - d.getTime()) / 1000);
  if (diff < 60)     return 'just now';
  if (diff < 3600)   return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400)  return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return d.toLocaleDateString();
}

/* ── Auth helpers ──────────────────────────────────────────────────────────── */
function logout() {
  localStorage.removeItem('fin_token');
  localStorage.removeItem('fin_user');
  _token = null;
  _user  = null;
  const tg = window.Telegram?.WebApp;
  if (tg?.close) { tg.close(); } else { window.location.href = '/login'; }
}

function initUserUI() {
  const badge = document.getElementById('userBadge');
  if (badge && _user) badge.textContent = _user.username || _user.first_name || 'User';
  const logoutBtn = document.getElementById('logoutBtn');
  if (logoutBtn) logoutBtn.onclick = logout;
}

/* ── ensureAuth — works in Telegram Mini App and plain browser ─────────────── */
async function ensureAuth() {
  if (_token) return;

  const tg = window.Telegram?.WebApp;
  if (tg?.initData) {
    const res = await fetch('/api/auth/telegram', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ init_data: tg.initData }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Telegram auth failed');
    _token = data.token;
    _user  = data.user;
    localStorage.setItem('fin_token', _token);
    localStorage.setItem('fin_user', JSON.stringify(_user));
    return;
  }

  window.location.replace('/login');
  throw new Error('Not authenticated');
}

/* ── Toast ─────────────────────────────────────────────────────────────────── */
function toast(msg, type = 'info', duration = 3000) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('show')));
  setTimeout(() => {
    el.classList.remove('show');
    setTimeout(() => el.remove(), 250);
  }, duration);
}

/* ── Button loading state ──────────────────────────────────────────────────── */
function btnLoad(btn, loading) {
  if (!btn) return;
  if (loading) {
    btn.dataset.origHtml = btn.innerHTML;
    btn.innerHTML = '<span class="spinner"></span>';
    btn.disabled = true;
  } else {
    if (btn.dataset.origHtml != null) btn.innerHTML = btn.dataset.origHtml;
    btn.disabled = false;
  }
}

/* ── API wrapper ───────────────────────────────────────────────────────────── */
async function api(path, options = {}, _retried = false) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (_token) headers.Authorization = `Bearer ${_token}`;
  const res = await fetch(path, { ...options, headers });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) {
    _token = null;
    localStorage.removeItem('fin_token');
    localStorage.removeItem('fin_user');
    if (!_retried && window.Telegram?.WebApp?.initData) {
      await ensureAuth();
      return api(path, options, true);
    }
    window.location.replace('/login');
    throw new Error('Session expired');
  }
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

/* ── Budget progress bar helper ────────────────────────────────────────────── */
function renderBudgets(items, containerId) {
  const el = $(containerId);
  if (!el) return;
  if (!items || items.length === 0) {
    el.innerHTML = '<p class="empty">No budgets set. Add budgets in Money Actions.</p>';
    return;
  }
  el.innerHTML = items.map((b) => {
    const fillClass = b.over ? 'over' : b.pct >= 75 ? 'warn' : '';
    const numsColor = b.over ? 'text-danger' : '';
    return `
      <div class="budget-item">
        <div class="budget-meta">
          <span class="cat">${b.category}</span>
          <span class="nums ${numsColor}">${money(b.spent)} / ${money(b.monthly_limit)} so'm</span>
        </div>
        <div class="progress-wrap">
          <div class="progress-fill ${fillClass}" style="width:${b.pct}%"></div>
        </div>
      </div>`;
  }).join('');
}

/* ── Nav active state ──────────────────────────────────────────────────────── */
function setActiveNav(page) {
  document.querySelectorAll('.topnav a').forEach((a) => {
    a.classList.toggle('active', a.dataset.page === page);
  });
}

/* ── AI Chatbot widget ─────────────────────────────────────────────────────── */
function initChat() {
  const fab = document.createElement('button');
  fab.className = 'ai-fab';
  fab.setAttribute('aria-label', 'Open Finance AI Chat');
  fab.title = 'Finance AI Chat';
  fab.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;gap:2px;line-height:1">
    <span style="font-size:21px">💬</span>
    <span style="font-size:8px;font-weight:800;letter-spacing:0.06em;color:inherit;opacity:0.9">AI</span>
  </div>`;

  const panel = document.createElement('div');
  panel.className = 'ai-panel';
  panel.innerHTML = `
    <div class="ai-panel-head">
      <div class="ai-avatar">✦</div>
      <div>
        <h4>Finance AI</h4>
        <p>Ask me anything about your finances</p>
      </div>
      <button class="ai-close-btn" aria-label="Close">✕</button>
    </div>
    <div class="ai-messages" id="aiMessages">
      <div class="ai-msg bot">Hi! I'm your personal finance assistant. I can see your account data — ask me about your spending, budgets, or financial goals!</div>
    </div>
    <div class="ai-input-row">
      <input id="aiInput" type="text" placeholder="Ask anything…" autocomplete="off">
      <button class="ai-send-btn" id="aiSendBtn" aria-label="Send">➤</button>
    </div>
  `;

  document.body.appendChild(fab);
  document.body.appendChild(panel);

  const HISTORY_KEY = 'fin_chat_history';
  let chatHistory = [];
  try { chatHistory = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'); } catch (_) { chatHistory = []; }
  let chatOpen = false;

  // Restore previous messages into the panel (skip system messages)
  if (chatHistory.length) {
    const msgs = panel.querySelector('#aiMessages');
    chatHistory.forEach((m) => {
      if (m.role === 'user' || m.role === 'assistant') {
        const el = document.createElement('div');
        el.className = `ai-msg ${m.role === 'user' ? 'user' : 'bot'}`;
        el.textContent = m.content;
        msgs.appendChild(el);
      }
    });
    msgs.scrollTop = msgs.scrollHeight;
  }

  function toggleChat() {
    chatOpen = !chatOpen;
    fab.classList.toggle('open', chatOpen);
    panel.classList.toggle('open', chatOpen);
    if (chatOpen) {
      setTimeout(() => panel.querySelector('#aiInput').focus(), 280);
    }
  }

  function appendMsg(role, text) {
    const msgs = panel.querySelector('#aiMessages');
    const el = document.createElement('div');
    el.className = `ai-msg ${role}`;
    el.textContent = text;
    msgs.appendChild(el);
    msgs.scrollTop = msgs.scrollHeight;
    return el;
  }

  async function sendMessage() {
    const input = panel.querySelector('#aiInput');
    const btn   = panel.querySelector('#aiSendBtn');
    const text  = input.value.trim();
    if (!text) return;

    input.value = '';
    btn.disabled = true;
    appendMsg('user', text);

    const typing = appendMsg('bot', '…');
    typing.classList.add('typing');

    try {
      const data = await api('/api/chat', {
        method: 'POST',
        body: JSON.stringify({ message: text, history: chatHistory }),
      });
      typing.classList.remove('typing');
      typing.textContent = data.reply || 'Sorry, I couldn\'t generate a response.';
      chatHistory.push({ role: 'user', content: text });
      chatHistory.push({ role: 'assistant', content: data.reply });
      if (chatHistory.length > 20) chatHistory = chatHistory.slice(-20);
      try { localStorage.setItem(HISTORY_KEY, JSON.stringify(chatHistory)); } catch (_) {}
    } catch (err) {
      typing.classList.remove('typing');
      typing.textContent = `Error: ${err.message}`;
    } finally {
      btn.disabled = false;
      input.focus();
    }
  }

  fab.addEventListener('click', toggleChat);
  panel.querySelector('.ai-close-btn').addEventListener('click', toggleChat);
  panel.querySelector('#aiSendBtn').addEventListener('click', sendMessage);
  panel.querySelector('#aiInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
}

// Auto-init chatbot after DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initChat);
} else {
  initChat();
}
