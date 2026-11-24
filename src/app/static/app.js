// Azure MS Cloud Chatbot - front end.
// Loaded as a file (not inline) so the Content-Security-Policy can block inline scripts.
'use strict';

// ---------------------------------------------------------------------------
// State and small helpers
// ---------------------------------------------------------------------------
function newSessionId() {
    if (window.crypto && crypto.randomUUID) return 'session-' + crypto.randomUUID();
    return 'session-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);
}

let currentSessionId = newSessionId();
let isSending = false;
let pendingAttachments = [];   // [{type: 'image'|'text', name, data_url?, content?}]

const MAX_ATTACHMENTS = 3;
const MAX_IMAGE_SIDE = 1600;               // screenshots are resized to this before upload
const MAX_IMAGE_BYTES = 4 * 1024 * 1024;
const MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024;
const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];
const TEXT_EXTENSIONS = ['txt', 'log', 'json', 'csv', 'md', 'xml', 'yaml', 'yml', 'ini', 'cfg', 'out', 'err'];

const $ = (id) => document.getElementById(id);
const chatStream = $('chatStream');
const userInput = $('userInput');

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
}

function icon(classes) {
    const i = document.createElement('i');
    i.className = classes;
    i.setAttribute('aria-hidden', 'true');
    return i;
}

function nearBottom() {
    return chatStream.scrollHeight - chatStream.scrollTop - chatStream.clientHeight < 120;
}

function scrollToBottom(force) {
    if (force || nearBottom()) chatStream.scrollTop = chatStream.scrollHeight;
}

// Lock the input while the bot is answering, so a second question
// can't be sent before the first reply arrives.
function setSending(sending) {
    isSending = sending;
    userInput.disabled = sending;
    $('sendBtn').disabled = sending;
    $('attachBtn').disabled = sending;
    userInput.placeholder = sending ? 'Waiting for the answer...' : 'Type a question, or paste a screenshot of the error...';
    userInput.classList.toggle('cursor-not-allowed', sending);
    document.querySelectorAll('.suggestion-chip').forEach(b => { b.disabled = sending; });
    if (!sending) userInput.focus();
}

// ---------------------------------------------------------------------------
// Markdown rendering (sanitized)
// ---------------------------------------------------------------------------
if (window.DOMPurify) {
    DOMPurify.addHook('afterSanitizeAttributes', node => {
        if (node.tagName === 'A') {
            node.setAttribute('target', '_blank');
            node.setAttribute('rel', 'noopener noreferrer');
        }
    });
}

function renderMarkdown(target, markdown) {
    if (!window.marked || !window.DOMPurify || typeof markdown !== 'string') {
        target.className = 'whitespace-pre-wrap';
        target.textContent = markdown || '';
        return;
    }
    try {
        const html = marked.parse(markdown, { gfm: true, breaks: true });
        target.innerHTML = DOMPurify.sanitize(html, { FORBID_TAGS: ['img', 'style', 'form', 'input'] });
        target.className = 'md-answer';
    } catch (err) {
        console.error('Markdown render failed:', err);
        target.className = 'whitespace-pre-wrap';
        target.textContent = markdown;
    }
}

// ---------------------------------------------------------------------------
// Sidebar and chat history
// ---------------------------------------------------------------------------
const sidebar = $('sidebar');
const toggleText = $('toggleText');
$('toggleHistoryBtn').addEventListener('click', () => {
    sidebar.classList.toggle('hidden');
    toggleText.textContent = sidebar.classList.contains('hidden') ? 'Show chat history' : 'Hide chat history';
});

function highlightActiveSession() {
    document.querySelectorAll('#historyList button[data-session-id]').forEach(btn => {
        const isActive = btn.dataset.sessionId === currentSessionId;
        btn.className = isActive
            ? 'flex-1 min-w-0 text-left p-2 pr-8 rounded bg-[#e5f1fb] text-[#004578] font-medium truncate cursor-pointer'
            : 'flex-1 min-w-0 text-left p-2 pr-8 rounded hover:bg-slate-100 text-slate-700 truncate cursor-pointer';
    });
}

async function deleteSession(sessionId, title) {
    const label = title && title.length > 60 ? title.slice(0, 60) + '…' : (title || 'this chat');
    if (!confirm(`Delete "${label}"?\n\nThis permanently removes the chat from your history.`)) return;
    try {
        const res = await fetch(`/history/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
        if (!res.ok && res.status !== 404) {
            alert(`Could not delete this chat (HTTP ${res.status}).`);
            return;
        }
        if (sessionId === currentSessionId) startNewChat();
        loadHistory();
    } catch (err) {
        console.error('Failed to delete session:', err);
        alert('Network error while deleting this chat.');
    }
}

async function loadHistory() {
    try {
        const res = await fetch('/history', { cache: 'no-store' });
        if (!res.ok) { console.error('Failed to load history:', res.status); return; }
        const sessions = await res.json();
        const container = $('historyList');
        container.innerHTML = '';

        if (!Array.isArray(sessions) || sessions.length === 0) {
            container.appendChild(el('div', 'text-slate-400 px-2 py-1', 'No past sessions'));
            return;
        }

        sessions.forEach(item => {
            const title = item.user_message || item.sessionId;
            const row = el('div', 'group relative flex items-center');

            const btn = el('button', '', title);
            btn.type = 'button';
            btn.dataset.sessionId = item.sessionId;
            btn.title = title;
            btn.addEventListener('click', () => { if (!isSending) resumeSession(item.sessionId); });

            const del = el('button', 'absolute right-1 p-1.5 rounded text-slate-400 hover:text-red-600 hover:bg-red-50 md:opacity-0 group-hover:opacity-100 focus:opacity-100');
            del.type = 'button';
            del.title = 'Delete chat';
            del.setAttribute('aria-label', 'Delete chat: ' + title);
            del.appendChild(icon('fa-regular fa-trash-can'));
            del.addEventListener('click', (e) => { e.stopPropagation(); deleteSession(item.sessionId, title); });

            row.append(btn, del);
            container.appendChild(row);
        });
        highlightActiveSession();
    } catch (err) {
        console.error('Failed to load history:', err);
    }
}

async function resumeSession(sessionId) {
    currentSessionId = sessionId;
    highlightActiveSession();
    clearAttachments();
    chatStream.innerHTML = '';
    const loading = createAssistantBubble();
    loading.setStatus('Loading conversation...');

    try {
        const res = await fetch(`/history/${encodeURIComponent(sessionId)}`, { cache: 'no-store' });
        loading.wrapper.remove();
        if (!res.ok) {
            createAssistantBubble().setText(`Could not load this chat (HTTP ${res.status}).`);
            return;
        }
        const turns = await res.json();
        if (!Array.isArray(turns) || turns.length === 0) {
            createAssistantBubble().setText('No messages were found for this session.');
            return;
        }
        turns.forEach(turn => {
            appendUserBubble(turn.user_message, (turn.attachments || []).map(a => ({ type: a.type, name: a.name })));
            const bubble = createAssistantBubble();
            bubble.applyMeta(turn);
            bubble.setText(turn.ai_message || '');
            bubble.finish(turn.turn_id, turn.user_message, turn);
        });
        scrollToBottom(true);
        if (window.innerWidth < 768) {
            sidebar.classList.add('hidden');
            toggleText.textContent = 'Show chat history';
        }
    } catch (err) {
        console.error('Failed to resume session:', err);
        chatStream.innerHTML = '';
        createAssistantBubble().setText('Network error while loading this chat.');
    }
}

function startNewChat() {
    if (isSending) return;
    currentSessionId = newSessionId();
    clearAttachments();
    chatStream.innerHTML = `
        <div id="heroSection" class="flex flex-col items-center justify-center h-full text-center my-auto pt-16">
            <img src="/static/logo.png" alt="Company Logo" class="h-12 object-contain mb-4">
            <h2 class="text-2xl font-bold text-slate-900 mb-2">Azure MS Cloud Chatbot. Start chatting.</h2>
            <p class="text-sm text-slate-500 max-w-lg leading-relaxed">
                Welcome to the Azure MS Cloud Chatbot! Please use it to help resolve customer support issues, find important wiki articles, and improve your workflow.
            </p>
        </div>
    `;
    highlightActiveSession();
}

// ---------------------------------------------------------------------------
// Attachments: screenshots and log files (paperclip, paste or drag and drop)
// ---------------------------------------------------------------------------
function showAttachError(message) {
    const box = $('attachError');
    box.textContent = message || '';
    box.classList.toggle('hidden', !message);
}

function fileExtension(name) {
    const parts = (name || '').toLowerCase().split('.');
    return parts.length > 1 ? parts.pop() : '';
}

function readAsDataURL(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
    });
}

function readAsText(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error);
        reader.readAsText(file);
    });
}

// Downscale large screenshots so uploads stay small; output JPEG (or PNG for small images)
async function prepareImage(file) {
    const dataUrl = await readAsDataURL(file);
    const img = await new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error('unreadable image'));
        image.src = dataUrl;
    });
    const scale = Math.min(1, MAX_IMAGE_SIDE / Math.max(img.width, img.height));
    if (scale === 1 && file.size <= 1024 * 1024 && file.type !== 'image/gif') return dataUrl;
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(img.width * scale);
    canvas.height = Math.round(img.height * scale);
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.85);
}

async function addFiles(fileList) {
    showAttachError('');
    for (const file of Array.from(fileList || [])) {
        if (pendingAttachments.length >= MAX_ATTACHMENTS) {
            showAttachError(`You can attach up to ${MAX_ATTACHMENTS} files per question.`);
            break;
        }
        const name = file.name || (file.type.startsWith('image/') ? 'pasted-screenshot.png' : 'file.txt');
        try {
            if (IMAGE_TYPES.includes(file.type)) {
                const dataUrl = await prepareImage(file);
                if (dataUrl.length * 0.75 > MAX_IMAGE_BYTES) {
                    showAttachError(`"${name}" is too large even after resizing.`);
                    continue;
                }
                pendingAttachments.push({ type: 'image', name, data_url: dataUrl });
            } else if (file.type.startsWith('text/') || TEXT_EXTENSIONS.includes(fileExtension(name))) {
                if (file.size > MAX_TEXT_FILE_BYTES) {
                    showAttachError(`"${name}" is larger than 2 MB. Attach only the relevant part of the log.`);
                    continue;
                }
                const content = await readAsText(file);
                if (!content.trim()) { showAttachError(`"${name}" is empty.`); continue; }
                pendingAttachments.push({ type: 'text', name, content });
            } else {
                showAttachError(`"${name}" isn't supported. Attach a screenshot (PNG, JPEG, WebP, GIF) or a text/log file.`);
            }
        } catch (err) {
            console.error('Attachment failed:', err);
            showAttachError(`"${name}" couldn't be read.`);
        }
    }
    renderAttachTray();
}

function clearAttachments() {
    pendingAttachments = [];
    showAttachError('');
    renderAttachTray();
}

function attachmentChip(att, onRemove) {
    const chip = el('div', 'flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-lg pl-1 pr-2 py-1 text-xs text-slate-700 max-w-[220px]');
    if (att.type === 'image' && att.data_url) {
        const thumb = el('img', 'h-8 w-8 object-cover rounded border border-slate-200');
        thumb.src = att.data_url;
        thumb.alt = att.name;
        chip.appendChild(thumb);
    } else {
        const box = el('span', 'h-8 w-8 flex items-center justify-center rounded bg-white border border-slate-200 text-slate-500');
        box.appendChild(icon(att.type === 'image' ? 'fa-regular fa-image' : 'fa-regular fa-file-lines'));
        chip.appendChild(box);
    }
    chip.appendChild(el('span', 'truncate', att.name));
    if (onRemove) {
        const remove = el('button', 'text-slate-400 hover:text-red-600 ml-1');
        remove.type = 'button';
        remove.title = 'Remove';
        remove.setAttribute('aria-label', 'Remove ' + att.name);
        remove.appendChild(icon('fa-solid fa-xmark'));
        remove.addEventListener('click', onRemove);
        chip.appendChild(remove);
    }
    return chip;
}

function renderAttachTray() {
    const tray = $('attachTray');
    tray.innerHTML = '';
    pendingAttachments.forEach((att, index) => {
        tray.appendChild(attachmentChip(att, () => {
            pendingAttachments.splice(index, 1);
            showAttachError('');
            renderAttachTray();
        }));
    });
    tray.classList.toggle('hidden', pendingAttachments.length === 0);
}

$('attachBtn').addEventListener('click', () => { if (!isSending) $('fileInput').click(); });
$('fileInput').addEventListener('change', async (e) => {
    await addFiles(e.target.files);
    e.target.value = '';
});
userInput.addEventListener('paste', (e) => {
    const files = Array.from(e.clipboardData?.files || []);
    if (files.length) {
        e.preventDefault();
        addFiles(files);
    }
});
const composer = $('composer');
composer.addEventListener('dragover', (e) => { e.preventDefault(); composer.classList.add('ring-2', 'ring-[#0078d4]'); });
composer.addEventListener('dragleave', () => composer.classList.remove('ring-2', 'ring-[#0078d4]'));
composer.addEventListener('drop', (e) => {
    e.preventDefault();
    composer.classList.remove('ring-2', 'ring-[#0078d4]');
    if (!isSending) addFiles(e.dataTransfer?.files);
});

// ---------------------------------------------------------------------------
// Message bubbles
// ---------------------------------------------------------------------------
function appendUserBubble(text, attachments) {
    const wrapper = el('div', 'flex justify-end');
    const bubble = el('div', 'bg-[#e5f1fb] text-[#004578] rounded-2xl rounded-tr-none px-4 py-3 max-w-xl text-sm leading-relaxed border border-[#c7e0f4] space-y-2');
    if (attachments && attachments.length) {
        const row = el('div', 'flex flex-wrap gap-2');
        attachments.forEach(a => row.appendChild(attachmentChip(a)));
        bubble.appendChild(row);
    }
    if (text) bubble.appendChild(el('div', 'whitespace-pre-wrap', text));
    wrapper.appendChild(bubble);
    chatStream.appendChild(wrapper);
    scrollToBottom(true);
    return wrapper;
}

function buildTicketsPanel(tickets) {
    if (!tickets || !tickets.length) return null;
    const panel = el('div', 'similar-tickets mt-2 pt-2 border-t border-slate-100 flex flex-col gap-2');
    panel.appendChild(el('div', 'text-[11px] font-semibold uppercase tracking-wide text-slate-500', 'Similar closed tickets'));
    tickets.forEach(t => {
        const card = el('div', 'rounded-lg border border-slate-200 bg-slate-50 px-3 py-2');
        const head = el('div', 'flex items-center gap-1.5 text-xs font-semibold text-slate-800');
        head.appendChild(icon('fa-solid fa-ticket text-[#0078d4]'));
        head.appendChild(el('span', 'truncate', t.title));
        card.appendChild(head);
        const body = el('div', 'text-xs text-slate-600 mt-1 leading-relaxed');
        if (t.has_resolution) body.appendChild(el('span', 'font-semibold text-slate-700', 'Resolution: '));
        body.appendChild(document.createTextNode(t.resolution || ''));
        card.appendChild(body);
        panel.appendChild(card);
    });
    return panel;
}

// Chats saved before numbered references existed only have the raw context text
function buildLegacyCitations(context) {
    if (!context) return null;
    const details = el('details', 'mt-2 pt-2 border-t border-slate-100 text-xs text-slate-500');
    details.appendChild(el('summary', 'cursor-pointer font-semibold text-[#0078d4] hover:underline', 'View sources'));
    details.appendChild(el('pre', 'mt-1 p-2 bg-slate-50 rounded whitespace-pre-wrap font-mono text-[11px]', context));
    return details;
}

// ---------------------------------------------------------------------------
// References: [n] markers in the answer + a list of the original source files
// ---------------------------------------------------------------------------
const CITATION_RE = /\[(\d{1,3})\]/g;
const HAS_CITATION = /\[\d{1,3}\]/;

// Chats saved before references were grouped by file have one section per reference
function refPassages(ref) {
    return ref.passages || [{ part: ref.part, content: ref.content || '' }];
}

// One entry per original file. Chats saved by the earlier version have one reference per
// section (several for the same file): merge those, and remember which old numbers map to which file.
function normalizeReferences(raw) {
    const files = [], byNumber = new Map(), byTitle = new Map();
    (raw || []).forEach(r => {
        if (r.passages) {                       // already one per file
            const f = { ...r, srcN: r.n };
            files.push(f);
            byNumber.set(r.n, f);
            return;
        }
        let f = byTitle.get(r.title);
        if (!f) {
            f = { n: files.length + 1, title: r.title, passages: [], srcN: r.n };
            byTitle.set(r.title, f);
            files.push(f);
        }
        f.passages.push({ part: r.part, content: r.content || '' });
        byNumber.set(r.n, f);
    });
    files.forEach(f => f.passages.sort((a, b) => (a.part ?? 1e9) - (b.part ?? 1e9)));
    return { files, byNumber };
}

function setReferences(ctx, raw) {
    const { files, byNumber } = normalizeReferences(raw);
    ctx.refs = files;
    ctx.byNumber = byNumber;
}

function referenceLabel(ref) {
    return ref.title || 'Source';
}

// Which files the answer cites (by file number, whatever numbers the answer used)
function citedFiles(text, ctx) {
    const cited = new Set();
    for (const m of (text || '').matchAll(CITATION_RE)) {
        const f = ctx.byNumber && ctx.byNumber.get(Number(m[1]));
        if (f) cited.add(f.n);
    }
    return cited;
}

// Turn "[2]" in the rendered answer into a small clickable superscript (text nodes only, never inside code)
function linkCitations(container, ctx) {
    if (!ctx.refs || !ctx.refs.length) return;
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
        acceptNode: (node) => (node.parentElement && node.parentElement.closest('code, pre, a, button'))
            ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    const nodes = [];
    while (walker.nextNode()) if (HAS_CITATION.test(walker.currentNode.nodeValue)) nodes.push(walker.currentNode);
    nodes.forEach(node => {
        const text = node.nodeValue;
        const frag = document.createDocumentFragment();
        let last = 0;
        let groupFiles = new Set();             // files already marked in this run of adjacent markers
        for (const m of text.matchAll(CITATION_RE)) {
            const between = text.slice(last, m.index);
            if (between.trim()) groupFiles = new Set();
            // Markers sit right after the word ("mismatch¹"); numbers with no matching source are dropped
            frag.appendChild(document.createTextNode(between.replace(/[ \t]+$/, '')));
            last = m.index + m[0].length;
            const ref = ctx.byNumber && ctx.byNumber.get(Number(m[1]));
            if (!ref || groupFiles.has(ref.n)) continue;   // "[1][2]" from the same file shows once
            groupFiles.add(ref.n);
            const n = ref.n;
            const marker = el('button', 'cite-marker', String(n));
            marker.type = 'button';
            marker.title = referenceLabel(ref);
            marker.setAttribute('aria-label', `Source ${n}: ${referenceLabel(ref)}`);
            marker.addEventListener('click', () => openReference(ref, ctx));
            frag.appendChild(marker);
        }
        if (last === 0) return;
        frag.appendChild(document.createTextNode(text.slice(last)));
        node.parentNode.replaceChild(frag, node);
    });
}

// One row per original file. Clicking opens the whole file with the sections used highlighted.
function buildReferences(ctx, answerText) {
    // Only the files the answer actually cites (older chats also stored files that weren't used)
    const cited = citedFiles(answerText, ctx);
    const refs = (ctx.refs || []).filter(r => cited.has(r.n));
    if (!refs.length) return null;
    const panel = el('div', 'references mt-2 pt-2 border-t border-slate-100');

    const head = el('div', 'flex items-center justify-between gap-2 mb-1.5');
    const toggle = el('button', 'inline-flex items-center gap-1.5 text-xs font-medium text-slate-600 hover:text-[#0078d4]');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', 'true');
    const caret = icon('fa-solid fa-chevron-down text-[10px] transition-transform');
    toggle.append(el('span', '', `Sources (${refs.length} file${refs.length === 1 ? '' : 's'})`), caret);
    head.append(toggle, el('span', 'text-[11px] text-slate-400', 'AI-generated content may be incorrect'));

    const list = el('div', 'flex flex-col');
    refs.forEach(ref => {
        const isCited = cited.has(ref.n);
        const row = el('button', 'reference-file flex items-start gap-2.5 min-w-0 w-full text-left rounded-md px-2 py-1.5 hover:bg-[#f3f9fd]'
            + (isCited ? '' : ' opacity-60'));
        row.type = 'button';
        row.title = `Open ${referenceLabel(ref)}` + (isCited ? '' : ' (retrieved, not cited in the answer)');
        row.append(
            el('span', 'shrink-0 w-5 h-5 rounded border border-slate-300 text-[10px] text-slate-600 flex items-center justify-center', String(ref.n)),
            icon('fa-regular fa-file-lines text-slate-400 shrink-0'),
            el('span', 'min-w-0 break-all text-xs text-[#0078d4] hover:underline', referenceLabel(ref)),   // full name, never cut off
        );
        row.addEventListener('click', () => openReference(ref, ctx));
        list.appendChild(row);
    });
    toggle.addEventListener('click', () => {
        const open = list.classList.toggle('hidden') === false;
        toggle.setAttribute('aria-expanded', String(open));
        caret.classList.toggle('-rotate-90', !open);
    });
    panel.append(head, list);
    return panel;
}

// Viewer: the original source file from the storage account, with the sections the answer used highlighted
let viewer = null;
function getViewer() {
    if (viewer) return viewer;
    const overlay = el('div', 'hidden fixed inset-0 z-50 bg-slate-900/40 flex items-center justify-center p-4');
    const dialog = el('div', 'bg-white rounded-xl shadow-xl w-full max-w-3xl max-h-[85vh] flex flex-col');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.setAttribute('aria-labelledby', 'viewerTitle');

    const header = el('div', 'flex items-start gap-3 px-4 py-3 border-b border-slate-200');
    const num = el('span', 'shrink-0 mt-0.5 w-6 h-6 rounded border border-slate-300 text-xs text-slate-600 flex items-center justify-center');
    const titles = el('div', 'min-w-0 flex-1');
    const title = el('div', 'font-semibold text-slate-900 text-sm break-all');
    title.id = 'viewerTitle';
    const subtitle = el('div', 'text-xs text-slate-500');
    titles.append(title, subtitle);
    const downloadBtn = el('button', 'shrink-0 text-xs border border-slate-300 bg-white hover:bg-slate-100 rounded px-2.5 py-1 disabled:opacity-40');
    downloadBtn.type = 'button';
    downloadBtn.append(icon('fa-solid fa-download mr-1'), document.createTextNode('Download'));
    const close = el('button', 'shrink-0 p-1.5 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100');
    close.type = 'button';
    close.setAttribute('aria-label', 'Close');
    close.appendChild(icon('fa-solid fa-xmark'));
    header.append(num, titles, downloadBtn, close);

    const body = el('div', 'overflow-auto px-4 py-3 outline-none');
    body.tabIndex = -1;
    const pre = el('pre', 'viewer-text whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-slate-800');
    body.appendChild(pre);
    dialog.append(header, body);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    viewer = { overlay, num, title, subtitle, downloadBtn, body, pre, current: null, lastFocus: null };
    const hide = () => {
        overlay.classList.add('hidden');
        if (viewer.lastFocus) viewer.lastFocus.focus();
    };
    close.addEventListener('click', hide);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) hide(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !overlay.classList.contains('hidden')) hide(); });
    downloadBtn.addEventListener('click', () => downloadSource());
    return viewer;
}

function sourceUrl(ctx, ref, download) {
    // srcN: the number the server stored for this file (differs from ref.n in chats saved by the earlier version)
    return `/source/${encodeURIComponent(ctx.sessionId)}/${encodeURIComponent(ctx.turnId)}/${ref.srcN ?? ref.n}` + (download ? '?download=true' : '');
}

function openReference(ref, ctx) {
    const v = getViewer();
    v.current = { ref, ctx };
    v.lastFocus = document.activeElement;
    v.num.textContent = String(ref.n);
    v.title.textContent = referenceLabel(ref);
    const ready = Boolean(ctx.turnId && ctx.sessionId);
    v.downloadBtn.disabled = !ready;
    v.downloadBtn.title = ready ? '' : 'Available once the answer has finished';
    v.overlay.classList.remove('hidden');
    if (ready) {
        showFullFile();
    } else {
        showSections('Showing the sections used. The full file opens once the answer has finished.');
    }
    v.downloadBtn.disabled ? v.body.focus() : v.downloadBtn.focus();
}

// Fallback: just the sections of the file that the answer was based on
function showSections(note) {
    const v = getViewer();
    const { ref } = v.current;
    v.subtitle.textContent = note;
    v.pre.innerHTML = '';
    refPassages(ref).forEach((p, i) => {
        if (i) v.pre.appendChild(document.createTextNode('\n\n…\n\n'));
        v.pre.appendChild(el('mark', 'viewer-highlight', p.content || ''));
    });
    v.body.scrollTop = 0;
}

async function showFullFile() {
    const v = getViewer();
    const { ref, ctx } = v.current;
    v.subtitle.textContent = 'Opening the file...';
    v.pre.textContent = '';
    try {
        const res = await fetch(sourceUrl(ctx, ref, false), { cache: 'no-store' });
        if (v.current.ref !== ref) return;              // user opened another file meanwhile
        const type = res.headers.get('content-type') || '';
        if (!res.ok) {
            let detail = `HTTP ${res.status}`;
            try { detail = (await res.json()).detail || detail; } catch (_) { /* not JSON */ }
            showSections(`Couldn't open the full file (${detail}). Showing the sections used instead.`);
            return;
        }
        if (!type.includes('application/json')) {      // not a text file: offer the download
            showSections("This file type can't be previewed here; use Download to open it. Showing the sections used.");
            return;
        }
        const data = await res.json();
        if (v.current.ref !== ref) return;
        renderHighlighted(data.text || '', refPassages(ref));
        const count = refPassages(ref).length;
        v.subtitle.textContent = `${data.path} · ${Math.max(1, Math.round(data.size / 1024))} KB · `
            + `${count} section${count === 1 ? '' : 's'} used in the answer ${count === 1 ? 'is' : 'are'} highlighted`;
    } catch (err) {
        console.error('Opening source failed:', err);
        showSections('Network error while opening the file. Showing the sections used instead.');
    }
}

// Write the full text with each section used wrapped in <mark> (DOM text nodes only, no HTML)
function renderHighlighted(full, passages) {
    const v = getViewer();
    const ranges = [];
    passages.forEach(p => {
        const content = (p.content || '').trim();
        const probe = content.slice(0, 200);
        const at = probe ? full.indexOf(probe) : -1;
        if (at >= 0) ranges.push([at, Math.min(at + content.length, full.length)]);
    });
    ranges.sort((a, b) => a[0] - b[0]);
    const merged = [];                                  // sections overlap, so merge them
    ranges.forEach(r => {
        const lastRange = merged[merged.length - 1];
        if (lastRange && r[0] <= lastRange[1]) lastRange[1] = Math.max(lastRange[1], r[1]);
        else merged.push(r.slice());
    });
    v.pre.innerHTML = '';
    let pos = 0, firstMark = null;
    merged.forEach(([a, b]) => {
        v.pre.appendChild(document.createTextNode(full.slice(pos, a)));
        const mark = el('mark', 'viewer-highlight', full.slice(a, b));
        v.pre.appendChild(mark);
        if (!firstMark) firstMark = mark;
        pos = b;
    });
    v.pre.appendChild(document.createTextNode(full.slice(pos)));
    if (firstMark) requestAnimationFrame(() => firstMark.scrollIntoView({ block: 'center' }));
    else v.body.scrollTop = 0;
}

async function downloadSource() {
    const v = getViewer();
    const { ref, ctx } = v.current;
    try {
        const res = await fetch(sourceUrl(ctx, ref, true), { cache: 'no-store' });
        if (!res.ok) {
            let detail = `HTTP ${res.status}`;
            try { detail = (await res.json()).detail || detail; } catch (_) { /* not JSON */ }
            v.subtitle.textContent = 'Could not download the file: ' + detail;
            return;
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = el('a');
        a.href = url;
        a.download = (ref.title || 'document').split('/').pop();
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
        console.error('Download failed:', err);
        v.subtitle.textContent = 'Network error while downloading the file.';
    }
}

async function postJSON(url, body) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) throw new Error((data && data.detail) || `HTTP ${res.status}`);
    return data;
}

// Thumbs up / down under each answer
function buildActions(sessionId, turnId, existingFeedback) {
    const bar = el('div', 'answer-actions mt-2 pt-2 border-t border-slate-100 flex flex-col gap-2');
    const row = el('div', 'flex items-center gap-1 flex-wrap');
    bar.appendChild(row);

    const thanks = el('span', 'text-[11px] text-slate-500 ml-1');
    const up = el('button', 'feedback-btn p-1.5 rounded text-slate-400 hover:text-[#0078d4] hover:bg-slate-100');
    const down = el('button', 'feedback-btn p-1.5 rounded text-slate-400 hover:text-red-600 hover:bg-slate-100');
    [up, down].forEach(b => { b.type = 'button'; });
    up.title = 'Helpful'; up.setAttribute('aria-label', 'Mark answer as helpful');
    down.title = 'Not helpful'; down.setAttribute('aria-label', 'Mark answer as not helpful');

    const commentBox = el('div', 'hidden flex flex-col gap-1.5');
    const comment = el('textarea', 'w-full text-xs border border-slate-300 rounded-md p-2 outline-none focus:ring-2 focus:ring-[#0078d4] resize-none');
    comment.rows = 2;
    comment.maxLength = 1000;
    comment.placeholder = 'What was wrong or missing? (optional — helps improve the knowledge base)';
    const sendComment = el('button', 'self-end text-xs bg-[#0078d4] hover:bg-[#106ebe] text-white rounded px-3 py-1', 'Send feedback');
    sendComment.type = 'button';
    commentBox.append(comment, sendComment);

    function paint(rating) {
        up.innerHTML = ''; down.innerHTML = '';
        up.appendChild(icon(rating === 'up' ? 'fa-solid fa-thumbs-up text-[#0078d4]' : 'fa-regular fa-thumbs-up'));
        down.appendChild(icon(rating === 'down' ? 'fa-solid fa-thumbs-down text-red-600' : 'fa-regular fa-thumbs-down'));
        up.setAttribute('aria-pressed', String(rating === 'up'));
        down.setAttribute('aria-pressed', String(rating === 'down'));
    }
    paint(existingFeedback && existingFeedback.rating);
    if (existingFeedback && existingFeedback.rating) thanks.textContent = 'Thanks for the feedback';

    async function send(rating, text) {
        try {
            await postJSON('/feedback', { session_id: sessionId, turn_id: turnId, rating, comment: text || null });
            paint(rating);
            thanks.textContent = 'Thanks for the feedback';
            return true;
        } catch (err) {
            thanks.textContent = 'Could not save feedback: ' + err.message;
            return false;
        }
    }
    up.addEventListener('click', async () => { commentBox.classList.add('hidden'); await send('up'); });
    down.addEventListener('click', async () => {
        if (await send('down')) {
            commentBox.classList.remove('hidden');
            comment.focus();
        }
    });
    sendComment.addEventListener('click', async () => {
        if (await send('down', comment.value.trim())) commentBox.classList.add('hidden');
    });


    row.append(up, down, thanks);
    bar.append(commentBox);
    return bar;
}

function buildSuggestions(questions) {
    if (!questions || !questions.length) return null;
    const wrap = el('div', 'suggestions mt-2 flex flex-col gap-1.5');
    wrap.appendChild(el('div', 'text-[11px] font-semibold uppercase tracking-wide text-slate-500', 'Suggested next questions'));
    const row = el('div', 'flex flex-wrap gap-2');
    questions.forEach(q => {
        const chip = el('button', 'suggestion-chip text-left text-xs bg-white border border-[#c7e0f4] text-[#004578] hover:bg-[#e5f1fb] rounded-full px-3 py-1 disabled:opacity-50', q);
        chip.type = 'button';
        chip.disabled = isSending;
        chip.addEventListener('click', () => {
            if (isSending) return;
            userInput.value = q;
            sendMessage();
        });
        row.appendChild(chip);
    });
    wrap.appendChild(row);
    return wrap;
}

// One assistant bubble that can be filled while streaming or from history
function createAssistantBubble() {
    const wrapper = el('div', 'flex justify-start');
    const bubble = el('div', 'bg-white text-slate-800 rounded-2xl rounded-tl-none px-4 py-3 max-w-2xl w-full sm:w-auto text-sm leading-relaxed border border-slate-200 shadow-sm space-y-2');
    const header = el('div', 'flex flex-col items-start gap-1');
    const status = el('div', 'text-slate-500 text-sm flex items-center gap-2');
    const answer = el('div', 'md-answer');
    const extras = el('div', '');
    bubble.append(header, status, answer, extras);
    wrapper.appendChild(bubble);
    chatStream.appendChild(wrapper);

    let pendingFrame = null;     // queued streaming redraw, if any
    let finished = false;        // set once the final text is shown; streaming redraws stop after this
    let latestText = '';
    const ctx = { sessionId: null, turnId: null, refs: [] };   // used by [n] markers and reference chips

    const api = {
        wrapper,
        setStatus(text) {
            status.innerHTML = '';
            if (!text) { status.classList.add('hidden'); return; }
            status.classList.remove('hidden');
            status.appendChild(icon('fa-solid fa-ellipsis fa-fade'));
            status.appendChild(el('span', '', text));
            scrollToBottom();
        },
        applyMeta(meta) {
            setReferences(ctx, meta.references || []);
            header.innerHTML = '';
            if (meta.search_query) {
                const searched = el('div', 'text-[11px] text-slate-400 italic truncate');
                searched.textContent = 'Searched for: ' + meta.search_query;
                searched.title = meta.search_query;
                header.appendChild(searched);
            }
        },
        // Streaming: re-render at most once per animation frame
        streamText(text) {
            if (finished) return;
            latestText = text;
            if (pendingFrame !== null) return;
            pendingFrame = requestAnimationFrame(() => {
                pendingFrame = null;
                if (finished) return;
                status.classList.add('hidden');
                renderMarkdown(answer, latestText);
                linkCitations(answer, ctx);
                answer.classList.add('streaming');
                scrollToBottom();
            });
        },
        // Final text: cancel any queued streaming redraw so it can't bring the blinking cursor back
        setText(text) {
            finished = true;
            if (pendingFrame !== null) { cancelAnimationFrame(pendingFrame); pendingFrame = null; }
            latestText = text;
            status.classList.add('hidden');
            renderMarkdown(answer, text);
            linkCitations(answer, ctx);
            answer.classList.remove('streaming');
            scrollToBottom();
        },
        finish(turnId, question, meta) {
            ctx.turnId = turnId || null;
            ctx.sessionId = meta.session_id || currentSessionId;
            if (meta.references && meta.references.length) setReferences(ctx, meta.references);
            extras.innerHTML = '';
            [buildTicketsPanel(meta.similar_tickets),
             ctx.refs.length ? buildReferences(ctx, latestText) : buildLegacyCitations(meta.context)]
                .forEach(node => { if (node) extras.appendChild(node); });
            if (turnId) extras.appendChild(buildActions(meta.session_id || currentSessionId, turnId, meta.feedback));
            scrollToBottom();
        },
        addSuggestions(questions) {
            const node = buildSuggestions(questions);
            if (node) { extras.appendChild(node); scrollToBottom(); }
        },
    };
    api.setStatus('');
    return api;
}

// ---------------------------------------------------------------------------
// Sending a question (streamed answer)
// ---------------------------------------------------------------------------
// While streaming, hide the model's internal "not found" signal; the server removes it from the final text
const NO_ANSWER_MARKER = 'NO_ANSWER_IN_SOURCES';
function visibleText(text) {
    const trimmed = text.trimStart();
    if (NO_ANSWER_MARKER.startsWith(trimmed)) return '';          // marker still arriving
    return text.replace(NO_ANSWER_MARKER, '').replace(/^[\s:]+/, '');
}

async function sendMessage() {
    const message = userInput.value.trim();
    const attachments = pendingAttachments.slice();
    if ((!message && attachments.length === 0) || isSending) return;
    setSending(true);
    const requestSessionId = currentSessionId;

    const hero = $('heroSection');
    if (hero) hero.remove();

    appendUserBubble(message, attachments);
    userInput.value = '';
    clearAttachments();

    const bubble = createAssistantBubble();
    bubble.setStatus(attachments.length
        ? 'Reading your attachment and searching the knowledge base...'
        : 'Searching the knowledge base...');

    let unlocked = false;
    const unlock = () => { if (!unlocked) { unlocked = true; setSending(false); } };

    try {
        const response = await fetch('/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            // Only the new question (and attachments) is sent; the server rebuilds the conversation
            body: JSON.stringify({ message, session_id: requestSessionId, attachments }),
        });

        if (!response.ok || !response.body) {
            let detail = `HTTP ${response.status}`;
            try { detail = (await response.json()).detail || detail; } catch (_) { /* not JSON */ }
            bubble.setText('**Error:** ' + detail);
            return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let text = '';
        let meta = {};

        const handle = (event) => {
            switch (event.type) {
                case 'meta':
                    meta = event;
                    bubble.applyMeta(event);
                    bubble.setStatus('Writing the answer...');
                    break;
                case 'delta':
                    text += event.text;
                    bubble.streamText(visibleText(text));
                    break;
                case 'done':
                    // The server sends the final text with only the sources it cites, and the matching badge
                    if (event.references) {
                        meta = { ...meta, references: event.references, confidence: event.confidence,
                                 similar_tickets: event.similar_tickets };
                        bubble.applyMeta(meta);
                    }
                    bubble.setText(event.content || visibleText(text));
                    bubble.finish(event.turn_id, message || 'Attached files', { ...meta, session_id: requestSessionId });
                    unlock();
                    loadHistory();
                    break;
                case 'suggestions':
                    bubble.addSuggestions(event.questions);
                    break;
                case 'blocked':
                    bubble.setText(event.content);
                    break;
                case 'error':
                    bubble.setText('**Error:** ' + event.message);
                    break;
            }
        };

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let newline;
            while ((newline = buffer.indexOf('\n')) >= 0) {
                const line = buffer.slice(0, newline).trim();
                buffer = buffer.slice(newline + 1);
                if (line) {
                    try { handle(JSON.parse(line)); } catch (err) { console.error('Bad stream line:', err); }
                }
            }
        }
        if (buffer.trim()) {
            try { handle(JSON.parse(buffer)); } catch (err) { console.error('Bad stream tail:', err); }
        }
    } catch (err) {
        console.error('Chat request failed:', err);
        bubble.setText('**Network error** connecting to the chatbot. Please try again.');
    } finally {
        unlock();
    }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------
userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (!isSending) sendMessage();
    }
});
$('sendBtn').addEventListener('click', () => { if (!isSending) sendMessage(); });
$('newChatSidebarBtn').addEventListener('click', startNewChat);
const newChatInlineBtn = $('newChatInlineBtn');
newChatInlineBtn.addEventListener('click', startNewChat);
newChatInlineBtn.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); startNewChat(); }
});

loadHistory();
