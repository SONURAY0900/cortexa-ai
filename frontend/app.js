/* =====================================================
   Cortexa AI — chat page
   Depends on common.js (Auth, Api, Toast, Modal, Prefs, el, …)
   ===================================================== */

"use strict";

const ROUTES = {
    DIRECT: { label: "Direct", status: "Writing the answer" },
    WIKIPEDIA: { label: "Wikipedia", status: "Reading Wikipedia" },
    TAVILY: { label: "Web", status: "Searching the web" },
    ARXIV: { label: "arXiv", status: "Searching research papers" },
    PDF_RAG: { label: "PDF", status: "Searching your PDF" },
    IMAGE: { label: "Image", status: "Analyzing the image" },
};

const SUGGESTIONS = [
    { route: "DIRECT", text: "Explain how neural networks learn" },
    { route: "WIKIPEDIA", text: "Who was Alan Turing?" },
    { route: "TAVILY", text: "What is the latest AI news today?" },
    { route: "ARXIV", text: "Find research papers about RAG" },
];

// Real backend ingestion stages, in order ("ocr" carries page progress).
const PDF_STAGE_LABELS = {
    upload: "Uploading",
    queued: "Waiting for another PDF to finish",
    extracting: "Extracting embedded text",
    ocr: "Reading scanned pages",
    chunking: "Chunking",
    embedding: "Embedding",
    indexing: "Indexing",
};

// No byte from the processing stream for this long means the connection
// is gone (the server sends a keep-alive line every 15 s).
const UPLOAD_STALL_MS = 60000;

function pageList(numbers) {
    return numbers.length > 1 ? `pages ${numbers.join(", ")}` : `page ${numbers[0]}`;
}

const IMAGE_MIME = ["image/jpeg", "image/png", "image/webp", "image/bmp"];
const IMAGE_EXT = /\.(jpe?g|png|webp|bmp)$/i;
const MAX_CONTEXT_FILES = 5;

const state = {
    conversationId: null,
    conversations: [],
    attachments: [],     // composer: [{ localId, kind, file, name, size, status, stage, progress, fileId, previewUrl, xhr, error }]
    contextFiles: [],    // PDFs kept for follow-up questions: [{ fileId, name }]
    streaming: null,     // AbortController of the active request
};

const $ = (id) => document.getElementById(id);

const ICON_PDF = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>';
const ICON_IMAGE = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>';


/* =====================================================
   MARKDOWN (escape first, then format)
   ===================================================== */

function renderInline(text) {
    return text
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
        .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) => {
            const safe = safeUrl(url.replace(/&amp;/g, "&"));
            return safe ? `<a href="${escapeHTML(safe)}" target="_blank" rel="noopener noreferrer">${label}</a>` : label;
        })
        .replace(/(^|[\s(])(https?:\/\/[^\s<)"]+)/g, (_, pre, url) => {
            const safe = safeUrl(url.replace(/&amp;/g, "&"));
            return safe ? `${pre}<a href="${escapeHTML(safe)}" target="_blank" rel="noopener noreferrer">${url}</a>` : pre + url;
        });
}

function renderMarkdown(source) {
    const blocks = [];
    // Formulas are set aside first (see math.js) and drawn by setText().
    const { text: plain, math } = MathRender.extractMath(source || "");
    let text = escapeHTML(plain);

    text = text.replace(/```([\w+-]*)\n?([\s\S]*?)(```|$)/g, (_, lang, code, end) => {
        // ```chart blocks become placeholders that setText() fills with a
        // chart (the JSON stays escaped inside the attribute).
        if (lang.toLowerCase() === "chart") {
            blocks.push(end
                ? `<div class="chart-slot" data-chart="${code.trim()}"></div>`
                : '<div class="chart-slot is-pending">Drawing chart…</div>');
            return `\n@@BLOCK${blocks.length - 1}@@\n`;
        }
        blocks.push(
            `<div class="code-block"><div class="code-head"><span>${lang || "code"}</span>` +
            `<button type="button" class="code-copy">Copy</button></div>` +
            `<pre><code>${code.replace(/\n$/, "")}</code></pre></div>`
        );
        return `\n@@BLOCK${blocks.length - 1}@@\n`;
    });

    const out = [];
    let list = null;
    let table = null;

    const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
    const closeTable = () => {
        if (!table) return;
        const [head, ...rows] = table;
        out.push(
            '<div class="table-wrap"><table><thead><tr>' +
            head.map((c) => `<th>${renderInline(c)}</th>`).join("") +
            "</tr></thead><tbody>" +
            rows.map((r) => "<tr>" + r.map((c) => `<td>${renderInline(c)}</td>`).join("") + "</tr>").join("") +
            "</tbody></table></div>"
        );
        table = null;
    };

    for (const line of text.split("\n")) {
        const trimmed = line.trim();
        const cells = /^\|.*\|$/.test(trimmed) ? trimmed.slice(1, -1).split("|").map((c) => c.trim()) : null;

        if (cells) {
            closeList();
            if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue;
            (table ||= []).push(cells);
            continue;
        }
        closeTable();

        const ul = line.match(/^\s*[-*•]\s+(.*)/);
        const ol = line.match(/^\s*(\d+)[.)]\s+(.*)/);
        if (ul || ol) {
            const tag = ul ? "ul" : "ol";
            // A list split by blank lines or other content restarts; keep the
            // written number (question 16 stays 16, not 1).
            if (list !== tag) { closeList(); out.push(ol && ol[1] !== "1" ? `<ol start="${Number(ol[1])}">` : `<${tag}>`); list = tag; }
            out.push(`<li>${renderInline(ul ? ul[1] : ol[2])}</li>`);
            continue;
        }
        closeList();

        const heading = line.match(/^(#{1,4})\s+(.*)/);
        if (heading) out.push(`<h${Math.min(heading[1].length + 2, 5)}>${renderInline(heading[2])}</h${Math.min(heading[1].length + 2, 5)}>`);
        else if (/^@@BLOCK\d+@@$/.test(trimmed)) out.push(trimmed);
        else if (/^&gt;\s?/.test(trimmed)) out.push(`<blockquote>${renderInline(trimmed.replace(/^&gt;\s?/, ""))}</blockquote>`);
        else if (/^(-{3,}|\*{3,})$/.test(trimmed)) out.push("<hr>");
        else if (trimmed) out.push(`<p>${renderInline(line)}</p>`);
    }
    closeList();
    closeTable();

    return out.join("")
        .replace(/@@BLOCK(\d+)@@/g, (_, i) => blocks[i])
        .replace(/@@MATH(\d+)@@/g, (token, i) => {
            const m = math[i];
            if (!m) return token;
            const tex = escapeHTML(m.tex);
            return `<span class="math${m.display ? " math-display" : ""}" data-tex="${tex}">${tex}</span>`;
        });
}

async function copyText(text, button) {
    try {
        await navigator.clipboard.writeText(text);
        const original = button.textContent;
        button.textContent = "Copied ✓";
        button.classList.add("copied");
        setTimeout(() => { button.textContent = original; button.classList.remove("copied"); }, 1500);
    } catch (_) {
        Toast.error("Copy failed. Please select the text manually.");
    }
}


/* =====================================================
   PRIVATE FILE MEDIA (authenticated, owner-only endpoint)
   ===================================================== */

const Media = {
    cache: new Map(),   // fileId -> Promise<objectURL>

    /** Object URL for one of the user's files (fetched with the session token). */
    url(fileId) {
        if (!Media.cache.has(fileId)) {
            const promise = Api.request(`/files/${encodeURIComponent(fileId)}/content`, { raw: true, quiet: true })
                .then((response) => response.blob())
                .then((blob) => URL.createObjectURL(blob));
            promise.catch(() => Media.cache.delete(fileId));
            Media.cache.set(fileId, promise);
        }
        return Media.cache.get(fileId);
    },

    remember(fileId, objectUrl) {
        if (fileId && objectUrl) Media.cache.set(fileId, Promise.resolve(objectUrl));
    },

    async openPdf(fileId, name) {
        // Open the tab synchronously so pop-up blockers allow it.
        const tab = window.open("", "_blank");
        try {
            const url = await Media.url(fileId);
            if (tab) tab.location.href = url;
            else window.location.assign(url);
        } catch (_) {
            tab?.close();
            Toast.error(`“${name}” is no longer available.`);
        }
    },

    lightbox(src, name) {
        Modal.open([
            el("div", { class: "lightbox" }, [
                el("img", { src, alt: name || "Image" }),
                el("div", { class: "lightbox-caption", text: name || "" }),
            ]),
        ], { wide: true, className: "modal-lightbox" });
    },
};

/** Attachment cards shown inside a sent user message. */
function messageAttachments(items) {
    if (!items?.length) return null;
    return el("div", { class: "msg-attachments" }, items.map((a, i) => {
        const isImage = a.file_type === "IMAGE";
        const style = `animation-delay:${i * 50}ms`;

        if (isImage) {
            const img = el("img", { alt: a.filename || "Image", decoding: "async" });
            const card = el("button", {
                class: "msg-image is-loading", type: "button", style,
                title: a.filename, "aria-label": `Open image ${a.filename || ""}`,
            }, [img]);
            const source = a.previewUrl ? Promise.resolve(a.previewUrl) : (a.file_id ? Media.url(a.file_id) : Promise.reject());
            source.then((url) => {
                img.src = url;
                img.addEventListener("load", () => card.classList.remove("is-loading"), { once: true });
                card.addEventListener("click", () => Media.lightbox(url, a.filename));
            }).catch(() => {
                card.classList.remove("is-loading");
                card.classList.add("is-missing");
                card.disabled = true;
                card.replaceChildren(el("span", { text: "Image unavailable" }));
            });
            return card;
        }

        const openable = Boolean(a.file_id);
        return el(openable ? "button" : "div", {
            class: `msg-file${openable ? "" : " is-static"}`, type: openable ? "button" : null, style,
            title: openable ? `Open ${a.filename}` : a.filename,
            onclick: openable ? () => Media.openPdf(a.file_id, a.filename) : null,
        }, [
            el("span", { class: "file-badge pdf", html: ICON_PDF }),
            el("span", { class: "file-meta" }, [
                el("span", { class: "file-name", text: a.filename || "Document" }),
                el("span", { class: "file-kind", text: "PDF" }),
            ]),
        ]);
    }));
}


/* =====================================================
   CHAT RENDERING
   ===================================================== */

const chat = {
    get inner() { return $("chatInner"); },
    get scroller() { return $("chatMessages"); },

    nearBottom() {
        const s = chat.scroller;
        return s.scrollHeight - s.scrollTop - s.clientHeight < 160;
    },

    scroll(force = false) {
        if (force || chat.nearBottom()) {
            chat.scroller.scrollTo({ top: chat.scroller.scrollHeight, behavior: force ? "auto" : "smooth" });
        }
    },

    welcome() {
        const loggedIn = Boolean(Auth.user);
        if (!Auth.resolved || Auth.status === "error") {
            // Never show the logged-out screen before the session is known.
            chat.inner.replaceChildren(el("section", { class: "hero-empty", "aria-busy": String(!Auth.resolved) }, [
                el("div", { class: "hero-logo" }, [el("img", { src: "/frontend/assets/logo/logo.svg", width: "56", height: "56", alt: "" })]),
                ...(Auth.status === "error" ? [
                    el("h1", { text: "Can't reach the sign-in service" }),
                    el("p", { text: Auth.error || MESSAGES.authUnavailable }),
                    el("div", { class: "hero-actions" }, [
                        el("button", { class: "btn btn-primary", type: "button", text: "Try again", onclick: () => Auth.retry() }),
                    ]),
                ] : [el("div", { class: "spinner", role: "status", "aria-label": "Loading" })]),
            ]));
            return;
        }
        chat.inner.replaceChildren(el("section", { class: "hero-empty" }, [
            el("div", { class: "hero-logo" }, [el("img", { src: "/frontend/assets/logo/logo.svg", width: "56", height: "56", alt: "" })]),
            el("h1", { text: loggedIn ? "What can I help you with?" : "Meet Cortexa AI" }),
            el("p", {
                text: loggedIn
                    ? "I pick the right tool for every question — web search, Wikipedia, research papers, your PDFs or images."
                    : "Ask anything as a guest. Log in to upload PDFs and images, use voice and keep your chats in your account.",
            }),
            el("div", { class: "chips" }, SUGGESTIONS.map((s, i) => el("button", {
                class: "chip",
                type: "button",
                style: `animation-delay:${80 + i * 60}ms`,
                onclick: () => sendMessage(s.text),
            }, [
                el("span", { class: `dot route-dot route-${s.route}` }),
                el("span", { class: "chip-text", text: s.text }),
                el("em", { text: ROUTES[s.route].label }),
            ]))),
        ]));
    },

    skeleton() {
        chat.inner.replaceChildren(...[0, 1, 2].map((i) => el("div", { class: `message ${i % 2 ? "bot-message" : "user-message"} skeleton-message` }, [
            el("div", { class: "message-content" }, [
                el("div", { class: "skeleton skeleton-line", style: "width:" + (i % 2 ? "70%" : "40%") }),
                el("div", { class: "skeleton skeleton-line", style: "width:" + (i % 2 ? "92%" : "25%") }),
            ]),
        ])));
    },

    removeHero() {
        chat.inner.querySelector(".hero-empty")?.remove();
    },

    timeLabel(value) {
        return new Date(value || Date.now()).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    },

    addUser(text, { time, attachments } = {}) {
        chat.removeHero();
        const content = el("div", { class: "message-content" }, [
            messageAttachments(attachments),
            text && !(attachments?.length && HIDDEN_QUESTIONS.has(text)) ? el("div", { class: "message-text", text }) : null,
            el("div", { class: "message-meta", text: chat.timeLabel(time) }),
        ]);
        chat.inner.append(el("div", { class: "message user-message" }, [content]));
        chat.scroll(true);
    },

    /** Assistant answer (plain text on the page, no box), used both for
     *  history and live streaming. The route shows in the footer trace. */
    addAssistant({ route = "" } = {}) {
        chat.removeHero();
        const body = el("div", { class: "answer-body" });
        const card = el("div", { class: "answer-card" }, [body]);
        const wrap = el("div", { class: "message bot-message" }, [
            el("div", { class: "message-content" }, [card]),
        ]);
        chat.inner.append(wrap);

        const charts = new Map();
        const api = {
            wrap, card, body, text: "",
            setRoute(name) {
                if (!ROUTES[name]) return;
                card.className = `answer-card route-${name}`;
            },
            setText(markdown) {
                api.text = markdown;
                body.innerHTML = renderMarkdown(markdown);
                MathRender.renderIn(body);
                body.querySelectorAll(".chart-slot[data-chart]").forEach((slot) => {
                    // Reuse the chart already drawn for this JSON, so streaming
                    // text after it doesn't redraw it on every chunk.
                    const json = slot.dataset.chart;
                    if (!charts.has(json)) {
                        const spec = Charts.parseChartSpec(json);
                        charts.set(json, spec ? Charts.createChart(spec)
                            : el("div", { class: "chart-error", text: "This answer included chart data that couldn't be read." }));
                    }
                    slot.replaceWith(charts.get(json));
                });
                body.querySelectorAll(".code-copy").forEach((button) => {
                    button.addEventListener("click", () => copyText(button.closest(".code-block").querySelector("code").textContent, button));
                });
            },
            setSources(sources) {
                card.querySelector(".sources")?.remove();
                const items = renderSources(sources);
                if (items) card.insertBefore(items, card.querySelector(".answer-foot"));
            },
            finish(routeName) {
                card.querySelector(".answer-foot")?.remove();
                const trace = ROUTES[routeName] ? el("div", { class: "trace" }, [
                    el("span", { class: "step", text: "You" }), el("i", { text: "→" }),
                    el("span", { class: "step", text: "Router" }), el("i", { text: "→" }),
                    el("span", { class: `route-badge route-${routeName}`, text: ROUTES[routeName].label }), el("i", { text: "→" }),
                    el("span", { class: "step", text: "Groq LLM" }),
                ]) : el("div");
                const copy = el("button", { class: "copy-btn", type: "button", text: "Copy" });
                copy.addEventListener("click", () => copyText(api.text.replace(/```chart[\s\S]*?(```|$)/gi, "").trim(), copy));
                card.append(el("div", { class: "answer-foot" }, [trace, copy]));
            },
            setError(message, onRetry) {
                card.className = "answer-card is-error";
                body.replaceChildren(
                    el("div", { class: "error-box" }, [
                        el("span", { class: "error-icon", html: ICONS.alert }),
                        el("div", {}, [
                            el("div", { class: "error-text", text: message }),
                            onRetry ? el("button", { class: "btn btn-outline btn-sm", type: "button", text: "Try again", onclick: () => { wrap.remove(); onRetry(); } }) : null,
                        ]),
                    ])
                );
            },
        };

        if (route) api.setRoute(route);
        return api;
    },
};

function renderSources(sources) {
    const web = [];
    const pdf = [];
    for (const source of sources || []) {
        if (source?.type === "pdf" || (source?.page_number && !source?.url)) pdf.push(source);
        else if (safeUrl(source?.url)) web.push(source);
    }
    if (!web.length && !pdf.length) return null;

    return el("div", { class: "sources" }, [
        el("div", { class: "sources-title", text: pdf.length ? "From your PDF" : "Sources" }),
        el("div", { class: "source-grid" }, [
            ...web.map((s, i) => {
                const url = safeUrl(s.url);
                let host = "";
                try { host = new URL(url).hostname.replace(/^www\./, ""); } catch (_) { /* ignore */ }
                return el("a", { class: "source-card", href: url, target: "_blank", rel: "noopener noreferrer", style: `animation-delay:${i * 60}ms` }, [
                    el("span", { class: "source-index", text: String(i + 1) }),
                    el("span", { class: "source-body" }, [
                        el("span", { class: "source-title", text: s.title || host || url }),
                        el("span", { class: "source-host", text: host }),
                    ]),
                ]);
            }),
            ...pdf.map((s, i) => el("div", { class: "source-card pdf", style: `animation-delay:${i * 60}ms` }, [
                el("span", { class: "source-index", text: s.page_number ? `p.${s.page_number}` : "PDF" }),
                el("span", { class: "source-body" }, [
                    el("span", { class: "source-title", text: s.document ? `${s.document} · ${s.title}` : (s.title || "PDF excerpt") }),
                    s.snippet ? el("span", { class: "source-snippet", text: s.snippet + (s.snippet.length >= 200 ? "…" : "") }) : null,
                ]),
            ])),
        ]),
    ]);
}


/* =====================================================
   THINKING / ROUTING INDICATOR
   ===================================================== */

function createThinking() {
    chat.removeHero();
    const status = el("span", { class: "thinking-label", text: "Sending" });
    const strip = el("div", { class: "route-strip" }, Object.entries(ROUTES).map(([key, r]) =>
        el("span", { class: `route-chip route-${key}`, "data-route": key }, [el("span", { class: "dot route-dot" }), r.label])
    ));
    const panel = el("div", { class: "thinking-card" }, [
        el("div", { class: "thinking-bar" }),
        el("div", { class: "thinking-row" }, [
            el("span", { class: "typing", "aria-hidden": "true" }, [el("i"), el("i"), el("i")]),
            status,
        ]),
        strip,
    ]);
    const wrap = el("div", { class: "message bot-message thinking", role: "status", "aria-live": "polite" }, [
        el("div", { class: "message-content" }, [panel]),
    ]);
    chat.inner.append(wrap);
    chat.scroll(true);

    const chips = [...strip.children];
    let index = 0;
    const scanner = setInterval(() => {
        chips.forEach((c) => c.classList.remove("scanning"));
        chips[index++ % chips.length].classList.add("scanning");
    }, 320);

    return {
        setStatus(text) {
            if (status.textContent === text) return;
            status.textContent = text;
            status.classList.remove("swap");
            void status.offsetWidth;
            status.classList.add("swap");
        },
        setRoute(route) {
            clearInterval(scanner);
            panel.classList.add("resolved");
            chips.forEach((c) => {
                c.classList.remove("scanning");
                c.classList.toggle("selected", c.dataset.route === route);
                c.classList.toggle("faded", c.dataset.route !== route);
            });
            this.setStatus(ROUTES[route]?.status || "Working on it");
        },
        remove() {
            clearInterval(scanner);
            wrap.remove();
        },
    };
}


/* =====================================================
   CONVERSATIONS
   ===================================================== */

const sidebar = {
    skeleton() {
        $("chatList").replaceChildren(...[70, 55, 80, 45].map((w) =>
            el("li", { class: "chat-item" }, [el("span", { class: "skeleton skeleton-line", style: `width:${w}%` })])
        ));
    },

    render() {
        const list = $("chatList");
        const needle = ($("chatSearch").value || "").trim().toLowerCase();
        const items = state.conversations.filter((c) => !needle || (c.title || "").toLowerCase().includes(needle));

        if (!Auth.resolved) return sidebar.skeleton();
        if (Auth.status === "error") {
            list.replaceChildren(el("li", { class: "empty-note", text: "Your chats will appear once you're reconnected." }));
            return;
        }
        if (!items.length) {
            const guest = !Auth.user;
            list.replaceChildren(el("li", {
                class: "empty-note",
                text: needle ? "No chats match your search."
                    : guest ? "Chatting as a guest. Log in to keep your chats in your account."
                    : "No chats yet. Ask your first question!",
            }));
            return;
        }

        list.replaceChildren(...items.map((c) => el("li", {
            class: "chat-item" + (c.id === state.conversationId ? " active" : ""),
            "data-id": c.id,
            tabindex: "0",
            role: "button",
            "aria-current": c.id === state.conversationId ? "true" : null,
        }, [
            el("span", { class: "chat-title", text: c.title || "New chat" }),
            el("button", {
                class: "chat-delete",
                type: "button",
                title: "Delete chat",
                "aria-label": `Delete chat ${c.title || ""}`,
                html: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>',
            }),
        ])));
    },

    async load({ openLatest = false } = {}) {
        // Signed-in users and guests both have chats; wait while the
        // session is still being checked or the sign-in service is down.
        if (Auth.status !== "signed-in" && Auth.status !== "signed-out") {
            state.conversations = [];
            sidebar.render();
            return;
        }
        if (!state.conversations.length) sidebar.skeleton();
        try {
            const data = await Api.get("/conversations");
            state.conversations = data.conversations || [];
            sidebar.render();
            if (openLatest && !state.conversationId && state.conversations.length) {
                await openConversation(state.conversations[0].id);
            }
        } catch (error) {
            $("chatList").replaceChildren(el("li", { class: "empty-note error", text: error.message }));
        }
    },

    close() {
        if (window.matchMedia("(max-width: 860px)").matches) {
            $("sidebar").classList.remove("open");
            $("overlay").hidden = true;
        }
    },
};

async function openConversation(id) {
    if (!id || state.streaming) return;
    state.conversationId = id;
    sidebar.render();
    sidebar.close();
    chat.skeleton();

    try {
        const data = await Api.get(`/conversations/${encodeURIComponent(id)}/messages`);
        chat.inner.replaceChildren();
        const contextPdfs = new Map();

        for (const m of data.messages || []) {
            const role = String(m.role || "").toUpperCase();
            if (role === "USER") {
                const attachments = m.metadata?.attachments || [];
                chat.addUser(m.content || "", { time: m.created_at, attachments });
                attachments
                    .filter((a) => a.file_type === "PDF" && a.file_id)
                    .forEach((a) => { contextPdfs.delete(a.file_id); contextPdfs.set(a.file_id, { fileId: a.file_id, name: a.filename }); });
            } else if (role === "ASSISTANT") {
                const card = chat.addAssistant({ route: m.route });
                card.setText(m.content || "");
                card.setSources(m.metadata?.sources || []);
                card.finish(m.route);
            }
        }

        // PDFs attached in this conversation stay available for follow-ups.
        context.set([...contextPdfs.values()].slice(-MAX_CONTEXT_FILES));
        if (!chat.inner.children.length) chat.welcome();
        chat.scroll(true);
    } catch (error) {
        chat.inner.replaceChildren();
        chat.addAssistant().setError(error.message, () => openConversation(id));
    }
}

function newChat() {
    if (state.streaming) return;
    state.conversationId = null;
    context.set([]);
    sidebar.render();
    sidebar.close();
    chat.welcome();
    $("userInput").focus();
}

async function deleteConversation(id) {
    const conversation = state.conversations.find((c) => c.id === id);
    const confirmed = await Modal.confirm({
        title: "Delete this chat?",
        message: `“${conversation?.title || "New chat"}” will be removed from your chat list.`,
        confirmText: "Delete chat",
        danger: true,
    });
    if (!confirmed) return;

    try {
        await Api.del(`/conversations/${encodeURIComponent(id)}`);
        state.conversations = state.conversations.filter((c) => c.id !== id);
        if (state.conversationId === id) newChat();
        sidebar.render();
        Toast.success("Chat deleted.");
    } catch (error) {
        Toast.error(error.message);
    }
}


/* =====================================================
   CONVERSATION CONTEXT (PDFs used for follow-up questions)
   ===================================================== */

const context = {
    set(files) {
        state.contextFiles = files;
        context.render();
    },

    render() {
        const bar = $("contextBar");
        if (!state.contextFiles.length) {
            bar.hidden = true;
            bar.replaceChildren();
            return;
        }
        bar.hidden = false;
        bar.replaceChildren(
            el("span", { class: "context-label", text: "Answering from" }),
            ...state.contextFiles.map((f) => el("span", { class: "context-chip" }, [
                el("span", { class: "file-badge pdf small", html: ICON_PDF }),
                el("span", { class: "context-name", text: f.name, title: f.name }),
                el("button", {
                    type: "button",
                    class: "context-remove",
                    "aria-label": `Stop using ${f.name}`,
                    title: "Stop using this PDF for follow-up questions",
                    html: ICONS.close,
                    onclick: () => context.set(state.contextFiles.filter((x) => x.fileId !== f.fileId)),
                }),
            ])),
        );
    },
};


/* =====================================================
   SENDING / STREAMING
   ===================================================== */

// A second click right after "Send" must not hit the button's "Stop" role.
const STOP_ARM_DELAY_MS = 800;

function setStreaming(controller) {
    state.streaming = controller;
    state.streamStartedAt = controller ? Date.now() : 0;
    const send = $("sendButton");
    send.classList.toggle("is-stop", Boolean(controller));
    send.setAttribute("aria-label", controller ? "Stop generating" : "Send message");
    send.title = controller ? "Stop generating" : "Send";
    $("attachFileButton").disabled = Boolean(controller);
}

async function ensureConversation(firstMessage) {
    if (state.conversationId) return state.conversationId;
    const data = await Api.post("/conversations", { title: firstMessage.slice(0, 60) || "New Chat" });
    state.conversationId = data.conversation.id;
    state.conversations = [data.conversation, ...state.conversations];
    sidebar.render();
    return state.conversationId;
}

/** The question sent when files are attached but nothing is typed. */
const DEFAULT_QUESTIONS = {
    image: "Answer the question in this image. If there is no question, describe the image briefly.",
    pdf: "Summarise this PDF.",
    pdfs: "Summarise these PDFs.",
    mixed: "Explain these files.",
};
// Sent for the user but not shown in their bubble (older wordings included,
// so chats saved before this change look the same).
const HIDDEN_QUESTIONS = new Set([
    ...Object.values(DEFAULT_QUESTIONS),
    "What is in this image? If it shows a question or problem, answer it.",
    "What is in this image? If it shows a question or problem, solve it step by step.",
]);

function defaultQuestion(files) {
    const pdfs = files.filter((f) => f.file_type === "PDF").length;
    if (pdfs && pdfs < files.length) return DEFAULT_QUESTIONS.mixed;
    if (pdfs) return pdfs > 1 ? DEFAULT_QUESTIONS.pdfs : DEFAULT_QUESTIONS.pdf;
    return DEFAULT_QUESTIONS.image;
}

async function sendMessage(text, { retry = null } = {}) {
    const input = $("userInput");
    const typed = (text ?? input.value).trim();

    if (state.streaming) return;
    if (!typed && !retry && attachments.pending()) {
        Toast.info("Please wait until your files have finished uploading.");
        return;
    }
    // Files alone can be sent (e.g. a pasted image); the server needs a question.
    const query = typed || (!retry && attachments.ready().length ? defaultQuestion(attachments.ready()) : "");
    if (!query) return;

    if (!Auth.resolved) { Toast.info("Checking your sign-in…"); return; }
    if (Auth.status === "error") { Toast.error(Auth.error || MESSAGES.authUnavailable); return; }

    // A retry re-sends the same attachments; otherwise use the composer.
    const outgoing = retry ? retry.attachments : attachments.ready();
    if (!retry && attachments.pending()) {
        Toast.info("Please wait until your files have finished uploading.");
        return;
    }

    if (!retry) {
        input.value = "";
        autoGrow();
        attachments.clearSent();
        chat.addUser(query, { attachments: outgoing });
    }

    // Earlier PDFs are kept for follow-up questions; a message with new
    // files is about those files, so the old ones are left out.
    const contextIds = outgoing.length ? [] : state.contextFiles.map((f) => f.fileId);

    const controller = new AbortController();
    setStreaming(controller);
    const thinking = createThinking();

    let card = null;
    let route = "";
    let full = "";
    let sources = [];
    let renderQueued = false;

    const flush = () => {
        renderQueued = false;
        if (card) {
            card.setText(full);
            chat.scroll();
        }
    };
    const ensureCard = () => {
        if (!card) {
            thinking.remove();
            card = chat.addAssistant({ route });
            card.body.classList.add("streaming");
        }
    };

    try {
        await ensureConversation(query);

        const response = await Api.request("/chat/stream", {
            method: "POST",
            raw: true,
            signal: controller.signal,
            json: {
                query,
                conversation_id: state.conversationId,
                attachments: outgoing.map((a) => a.file_id),
                context_file_ids: contextIds,
                retry: Boolean(retry),
            },
        });

        // New files start a new topic: follow-ups use the PDFs sent now (none
        // after an image-only message), not ones from earlier messages.
        if (outgoing.length) {
            context.set(outgoing.filter((a) => a.file_type === "PDF").map((a) => ({ fileId: a.file_id, name: a.filename })));
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const events = buffer.split("\n\n");
            buffer = events.pop();

            for (const raw of events) {
                if (!raw.startsWith("data:")) continue;
                let data;
                try { data = JSON.parse(raw.slice(5).trim()); } catch (_) { continue; }

                if (data.type === "status") {
                    if (!card) thinking.setStatus(data.label || "Working on it");
                } else if (data.type === "route") {
                    route = String(data.route || "").toUpperCase();
                    thinking.setRoute(route);
                } else if (data.type === "content") {
                    ensureCard();
                    full += data.content || "";
                    if (!renderQueued) {
                        renderQueued = true;
                        requestAnimationFrame(flush);
                    }
                } else if (data.type === "sources") {
                    sources = data.sources || [];
                } else if (data.type === "error") {
                    throw new ApiError(data.content || "Something went wrong while processing your request.", 502);
                }
            }
        }

        ensureCard();
        flush();
        card.body.classList.remove("streaming");
        card.setSources(sources);
        card.finish(route);
        sidebar.load();

    } catch (error) {
        thinking.remove();
        if (error.name === "AbortError") {
            if (!card) card = chat.addAssistant({ route });
            card.body.classList.remove("streaming");
            card.setText(full ? full + "\n\n*Stopped.*" : "*Stopped before an answer was generated.*");
            card.finish(route);
        } else {
            if (!card) card = chat.addAssistant();
            card.body.classList.remove("streaming");
            card.setError(error.message || MESSAGES.generic, () => sendMessage(query, { retry: { attachments: outgoing } }));
        }
    } finally {
        setStreaming(null);
        chat.scroll();
        input.focus();
    }
}

function autoGrow() {
    const input = $("userInput");
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 180) + "px";
}


/* =====================================================
   PDF FIRST-PAGE PREVIEWS
   PDF.js is loaded from cdnjs only when the first PDF is attached. The
   preview is cosmetic: if anything fails the card keeps its PDF icon and
   the upload is unaffected.
   ===================================================== */

const PDFJS_URL = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js";
const PDFJS_SRI = "sha384-/1qUCSGwTur9vjf/z9lmu/eCUYbpOTgSjmpbMQZ1/CtX2v/WcAIKqRv+U1DUCG6e";
const PDFJS_WORKER_URL = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
const PDFJS_WORKER_SRI = "sha384-SnzOobpRMLXZ52iJvZm/C0fYw0OQemTXzTjIsdsfMcrCtCEe9qgzxTd3RSklO5x2";

const PdfPreview = {
    ready: null,

    load() {
        PdfPreview.ready ||= (async () => {
            await new Promise((resolve, reject) => {
                const script = el("script", { src: PDFJS_URL, integrity: PDFJS_SRI, crossorigin: "anonymous" });
                script.onload = resolve;
                script.onerror = () => reject(new Error("PDF.js could not be loaded"));
                document.head.append(script);
            });
            // The worker is fetched with an integrity check and run from a
            // same-origin blob URL.
            const response = await fetch(PDFJS_WORKER_URL, { integrity: PDFJS_WORKER_SRI, mode: "cors" });
            if (!response.ok) throw new Error("PDF.js worker could not be loaded");
            window.pdfjsLib.GlobalWorkerOptions.workerSrc = URL.createObjectURL(await response.blob());
            return window.pdfjsLib;
        })().catch((error) => {
            PdfPreview.ready = null;
            throw error;
        });
        return PdfPreview.ready;
    },

    /** The first page as a small JPEG data URL, or null. */
    async firstPage(file, width = 220) {
        try {
            const pdfjs = await PdfPreview.load();
            const data = new Uint8Array(await file.arrayBuffer());
            // isEvalSupported: false keeps font code from running as script
            // (CVE-2024-4367).
            const doc = await pdfjs.getDocument({ data, isEvalSupported: false }).promise;
            try {
                const page = await doc.getPage(1);
                const scale = (width * Math.min(window.devicePixelRatio || 1, 2)) / page.getViewport({ scale: 1 }).width;
                const viewport = page.getViewport({ scale });
                const canvas = document.createElement("canvas");
                canvas.width = Math.ceil(viewport.width);
                canvas.height = Math.ceil(viewport.height);
                const context = canvas.getContext("2d");
                context.fillStyle = "#ffffff";
                context.fillRect(0, 0, canvas.width, canvas.height);
                // "print" renders in one go; the default "display" intent
                // paces itself with requestAnimationFrame, which browsers
                // pause in background tabs, so the preview would stall.
                await page.render({ canvasContext: context, viewport, intent: "print" }).promise;
                return canvas.toDataURL("image/jpeg", 0.82);
            } finally {
                doc.destroy();
            }
        } catch (_) {
            return null;
        }
    },
};


/* =====================================================
   COMPOSER ATTACHMENTS (photos & files)
   One pipeline for the file picker, drag & drop and paste.
   ===================================================== */

const attachments = {
    seq: 0,

    limits() {
        const l = Auth.config?.limits || {};
        return { pdf: l.pdf_mb || 25, image: l.image_mb || 10, max: l.attachments_per_message || 10 };
    },

    pending() {
        return state.attachments.some((a) => a.status === "uploading" || a.status === "processing");
    },

    /** References of uploaded, ready files (what a message carries). */
    ready() {
        return state.attachments.filter((a) => a.status === "ready").map((a) => ({
            file_id: a.fileId,
            filename: a.name,
            file_type: a.kind === "pdf" ? "PDF" : "IMAGE",
            mime_type: a.file.type || (a.kind === "pdf" ? "application/pdf" : ""),
            previewUrl: a.previewUrl,
        }));
    },

    classify(file) {
        const name = (file.name || "").toLowerCase();
        if (file.type === "application/pdf" || name.endsWith(".pdf")) return "pdf";
        if (IMAGE_MIME.includes(file.type) || IMAGE_EXT.test(name)) return "image";
        return null;
    },

    add(fileList) {
        if (!Auth.requireUser("Please log in to add photos and files.")) return;
        const files = [...fileList];
        const { pdf, image, max } = attachments.limits();
        let room = max - state.attachments.length;

        for (const original of files) {
            const kind = attachments.classify(original);
            if (!kind) { Toast.error(`“${original.name || "This file"}” is not supported. Add a PDF or a JPG, PNG, WEBP or BMP image.`); continue; }
            if (!original.size) { Toast.error(`“${original.name}” is empty.`); continue; }
            const limit = kind === "pdf" ? pdf : image;
            if (original.size > limit * 1024 * 1024) { Toast.error(`“${original.name}” is larger than ${limit} MB.`); continue; }
            if (room <= 0) { Toast.error(`You can attach up to ${max} files per message.`); break; }
            const duplicate = state.attachments.find((a) => a.status !== "error" && a.name === original.name
                && a.size === original.size && a.file.lastModified === original.lastModified);
            if (duplicate) { Toast.info(`“${original.name}” is already attached.`); continue; }

            // Clipboard images often arrive as "image.png"; give them a clear, unique name.
            const file = kind === "image" && (!original.name || original.name === "image.png")
                ? new File([original], `pasted-image-${new Date().toISOString().replace(/[:.]/g, "-")}.${(original.type.split("/")[1] || "png").replace("jpeg", "jpg")}`, { type: original.type || "image/png" })
                : original;

            const entry = {
                localId: ++attachments.seq,
                kind, file,
                name: file.name,
                size: file.size,
                status: "uploading",
                stage: "upload",
                progress: 0,
                previewUrl: kind === "image" ? URL.createObjectURL(file) : null,
            };
            state.attachments.push(entry);
            room--;
            attachments.upload(entry);
            if (kind === "pdf") {
                PdfPreview.firstPage(file).then((url) => {
                    if (!url || !state.attachments.includes(entry)) return;
                    entry.thumbUrl = url;
                    attachments.render();
                });
            }
        }
        attachments.render();
    },

    async remove(localId) {
        const entry = state.attachments.find((a) => a.localId === localId);
        if (!entry) return;
        entry.xhr?.abort();
        state.attachments = state.attachments.filter((a) => a !== entry);
        const node = document.querySelector(`[data-attachment="${localId}"]`);
        if (node) {
            node.classList.add("leaving");
            setTimeout(() => attachments.render(), 180);
        } else {
            attachments.render();
        }
        if (entry.previewUrl) URL.revokeObjectURL(entry.previewUrl);
        // Uploaded but never sent: remove it from the server too. A PDF that
        // is still processing is marked deleted; the server discards its
        // document when processing finishes.
        if (entry.fileId) {
            Api.del(`/files/${encodeURIComponent(entry.fileId)}`, undefined, { quiet: true }).catch(() => { /* best effort */ });
        }
    },

    /** After sending: drop the tray but keep image previews for the sent message. */
    clearSent() {
        state.attachments.filter((a) => a.status === "ready" && a.fileId && a.previewUrl)
            .forEach((a) => Media.remember(a.fileId, a.previewUrl));
        state.attachments
            .filter((a) => a.status !== "ready" && a.previewUrl)
            .forEach((a) => URL.revokeObjectURL(a.previewUrl));
        state.attachments.forEach((a) => a.xhr?.abort());
        state.attachments = [];
        attachments.render();
    },

    statusLabel(a) {
        if (a.status === "error") return a.error;
        if (a.status === "ready") {
            if (a.kind !== "pdf") return "Image";
            const s = a.summary;
            if (a.partial && s?.failed_pages?.length) return `Partly readable · ${pageList(s.failed_pages)} not searchable`;
            return s?.pages_total ? `PDF · ${s.pages_total} page${s.pages_total === 1 ? "" : "s"}` : "PDF";
        }
        if (a.stage === "upload") return `Uploading ${a.progress}%`;
        if (a.stage === "ocr" && a.ocr) return `Reading scanned page ${a.ocr.current} of ${a.ocr.total} (OCR)`;
        return PDF_STAGE_LABELS[a.stage] || "Processing";
    },

    /** Upload a failed file again (same File object, fresh state). */
    retry(localId) {
        const entry = state.attachments.find((a) => a.localId === localId);
        if (!entry || entry.status !== "error") return;
        Object.assign(entry, { status: "uploading", stage: "upload", progress: 0, error: null, fileId: null,
                               summary: null, partial: false, ocr: null, retryable: false, authRetried: false });
        attachments.upload(entry);
        attachments.render();
    },

    render() {
        const tray = $("attachmentTray");
        if (!state.attachments.length) {
            tray.hidden = true;
            tray.replaceChildren();
            updateSendState();
            return;
        }
        tray.hidden = false;

        const existing = new Map([...tray.children].map((n) => [n.dataset.attachment, n]));
        const nodes = state.attachments.map((a) => {
            const node = existing.get(String(a.localId)) || el("div", { class: `attach-card ${a.kind}`, "data-attachment": String(a.localId) });
            node.className = `attach-card ${a.kind} is-${a.status}${a.partial ? " is-partial" : ""}`;
            node.title = a.summary ? `${a.summary.pages_total} page(s) · ${a.summary.pages_indexed.length} searchable`
                + (a.summary.pages_needing_ocr ? ` · ${a.summary.pages_needing_ocr} read with OCR` : "") : "";
            const busy = a.status === "uploading" || a.status === "processing";
            const picture = a.kind === "image" ? a.previewUrl : a.thumbUrl;
            const label = attachments.statusLabel(a);

            const preview = el("div", { class: "attach-preview" }, [
                picture
                    ? el("img", { src: picture, alt: "", decoding: "async" })
                    : el("span", { class: "attach-placeholder", html: ICON_PDF }),
                busy ? el("div", { class: "attach-overlay", role: "status" }, [
                    el("span", { class: "spinner", "aria-hidden": "true" }),
                    el("span", { class: "attach-status", text: label }),
                ]) : null,
                a.status === "error" ? el("div", { class: "attach-overlay is-error", role: "alert" }, [
                    el("span", { class: "attach-status", text: label }),
                    a.retryable !== false ? el("button", {
                        class: "attach-retry", type: "button", text: "Try again",
                        "aria-label": `Upload ${a.name} again`,
                        onclick: () => attachments.retry(a.localId),
                    }) : null,
                ]) : null,
                a.status === "ready" && a.partial ? el("span", { class: "attach-flag", text: label }) : null,
                busy ? el("span", { class: `attach-progress${a.stage === "upload" ? "" : " indeterminate"}` }, [
                    el("span", { style: a.stage === "upload" ? `width:${a.progress}%` : null }),
                ]) : null,
            ]);

            node.replaceChildren(...[
                preview,
                el("div", { class: "attach-caption" }, [
                    el("span", { class: `file-badge small ${a.kind}`, html: a.kind === "pdf" ? ICON_PDF : ICON_IMAGE }),
                    el("span", { class: "file-name", text: a.name, title: a.name }),
                ]),
                el("button", {
                    class: "attach-remove", type: "button",
                    "aria-label": busy ? `Cancel uploading ${a.name}` : `Remove ${a.name}`,
                    title: busy ? "Cancel upload" : "Remove",
                    html: ICONS.close,
                    onclick: () => attachments.remove(a.localId),
                }),
            ]);
            return node;
        });
        tray.replaceChildren(...nodes);
        updateSendState();
    },

    async upload(entry) {
        const isPdf = entry.kind === "pdf";
        const token = await Auth.token();
        if (!state.attachments.includes(entry)) return;

        const xhr = new XMLHttpRequest();
        entry.xhr = xhr;
        xhr.open("POST", isPdf ? "/upload/pdf?stream=true" : "/upload/image");
        if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

        const update = (patch) => {
            if (!state.attachments.includes(entry)) return;
            Object.assign(entry, patch);
            attachments.render();
        };
        const failMessage = isPdf ? "PDF processing failed. Please try another file." : "Image upload failed. Please try again.";
        let stallTimer = null;
        const finish = () => { clearTimeout(stallTimer); entry.xhr = null; };
        const fail = (message, extra = {}) => {
            if (entry.status === "error" || entry.status === "ready") return;
            finish();
            update({ status: "error", error: message || failMessage, ...extra });
        };
        const alive = () => {
            clearTimeout(stallTimer);
            stallTimer = setTimeout(() => {
                xhr.abort();
                fail("The connection to the server was lost. Please try again.");
            }, UPLOAD_STALL_MS);
        };
        alive();

        xhr.upload.onprogress = (event) => {
            alive();
            if (event.lengthComputable) update({ progress: Math.round((event.loaded / event.total) * 100) });
        };
        xhr.upload.onload = () => update(isPdf ? { status: "processing", stage: "extracting", progress: 100 } : { progress: 100 });

        let consumed = 0;
        const readEvents = () => {
            const text = xhr.responseText;
            // The last element is the unfinished remainder ("" when the
            // text ends with a newline); only the others are complete lines.
            const complete = text.slice(consumed).split("\n").slice(0, -1);
            for (const line of complete) {
                consumed += line.length + 1;
                if (!line.trim()) continue;
                let data;
                try { data = JSON.parse(line); } catch (_) { continue; }
                if (data.file_id) entry.fileId = data.file_id;
                if (data.type === "stage") {
                    update({
                        status: "processing",
                        stage: data.stage === "uploaded" ? "extracting" : data.stage,
                        ocr: data.stage === "ocr" ? { current: data.current, total: data.total, page: data.page } : entry.ocr,
                    });
                }
                if (data.type === "done") {
                    finish();
                    update({ status: "ready", fileId: data.file_id, summary: data.summary || null, partial: Boolean(data.partial) });
                    if (data.partial) Toast.show(data.message || "Some pages could not be read.", "info", 9000);
                }
                if (data.type === "error") {
                    const failed = data.summary?.failed_pages || [];
                    const detail = (data.detail || failMessage)
                        + (failed.length && data.reason === "ocr_failed" ? ` (${pageList(failed)} failed)` : "");
                    fail(detail, { retryable: data.retryable !== false, summary: data.summary || null });
                }
            }
        };

        xhr.onprogress = () => {
            alive();
            if (isPdf && xhr.status === 200) readEvents();
        };

        xhr.onload = async () => {
            clearTimeout(stallTimer);
            if (xhr.status === 401) {
                entry.xhr = null;
                // Expired access token: refresh once and upload again.
                const refreshed = entry.authRetried ? "invalid" : await Auth.refresh(token);
                if (refreshed === "ok") {
                    entry.authRetried = true;
                    attachments.upload(entry);
                    return;
                }
                if (refreshed === "unavailable") {
                    fail(MESSAGES.authUnavailable, { retryable: true });
                    return;
                }
                fail(MESSAGES.session);
                Auth.expire();
                return;
            }
            if (xhr.status !== 200) {
                let detail = null;
                try { detail = JSON.parse(xhr.responseText).detail; } catch (_) { /* not JSON */ }
                const message = typeof detail === "string" ? detail
                    : xhr.status === 503 ? "The server is busy. Please try again." : failMessage;
                fail(message, { retryable: xhr.status >= 500 || xhr.status === 0 || xhr.status === 408 || xhr.status === 429 });
                return;
            }
            if (isPdf) {
                readEvents();
                if (entry.status !== "ready" && entry.status !== "error") fail("The upload ended before processing finished. Please try again.");
            } else {
                try {
                    const data = JSON.parse(xhr.responseText);
                    if (!data?.file_id && !data?.image_id) throw new Error("malformed");
                    finish();
                    update({ status: "ready", fileId: data.file_id || data.image_id });
                } catch (_) {
                    fail(failMessage);
                }
            }
        };
        xhr.onerror = () => fail(MESSAGES.network);
        xhr.onabort = () => clearTimeout(stallTimer);

        const form = new FormData();
        form.append("file", entry.file, entry.name);
        xhr.send(form);
        attachments.render();
    },
};

function updateSendState() {
    const pending = attachments.pending();
    $("composer").classList.toggle("has-pending", pending);
    $("sendButton").title = state.streaming ? "Stop generating" : pending ? "Waiting for files to finish" : "Send";
}

function initAttachments() {
    const input = $("fileInput");
    $("attachFileButton").addEventListener("click", () => {
        if (!Auth.requireUser("Please log in to add photos and files.")) return;
        input.click();
    });
    input.addEventListener("change", () => {
        if (input.files?.length) attachments.add(input.files);
        input.value = "";
    });

    // Ctrl+V: files/screenshots become attachments; text pastes normally.
    $("userInput").addEventListener("paste", (event) => {
        const data = event.clipboardData;
        if (!data) return;
        const files = [...data.items || []]
            .filter((item) => item.kind === "file")
            .map((item) => item.getAsFile())
            .filter(Boolean);
        if (!files.length) return;
        const hasText = [...data.types].includes("text/plain") && data.getData("text/plain").trim();
        if (!hasText) event.preventDefault();
        attachments.add(files);
    });

    const main = $("main");
    const zone = $("dropZone");
    let depth = 0;
    const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");

    main.addEventListener("dragenter", (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        depth++;
        zone.hidden = false;
    });
    main.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
    main.addEventListener("dragleave", () => {
        depth = Math.max(0, depth - 1);
        if (!depth) zone.hidden = true;
    });
    main.addEventListener("drop", (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        depth = 0;
        zone.hidden = true;
        if (e.dataTransfer.files?.length) attachments.add(e.dataTransfer.files);
    });
}


/* =====================================================
   VOICE INPUT (Listening → Transcribing → Sending → Generating)
   ===================================================== */

const voice = {
    recorder: null,
    stream: null,
    chunks: [],
    timer: null,
    started: 0,

    setState(mode, label = "") {
        const button = $("voiceButton");
        const panel = $("voicePanel");
        button.dataset.state = mode;
        panel.hidden = mode === "idle";
        $("userInput").classList.toggle("dimmed", mode !== "idle");
        $("voiceLabel").textContent = label;
        button.setAttribute("aria-label", mode === "listening" ? "Stop recording" : "Voice input");
        button.title = mode === "listening" ? "Stop recording" : "Voice input";
        button.disabled = mode === "transcribing";
        panel.dataset.state = mode;
    },

    mimeType() {
        if (!window.MediaRecorder) return "";
        return ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"]
            .find((t) => MediaRecorder.isTypeSupported(t)) || "";
    },

    async start() {
        if (!Auth.requireUser("Please log in to use voice input.")) return;
        if (state.streaming) return;
        if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
            Toast.error("Voice input is not supported by this browser.");
            return;
        }
        try {
            voice.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch (error) {
            Toast.error(error?.name === "NotAllowedError"
                ? "Microphone access was blocked. Allow it in your browser's site settings and try again."
                : "No microphone could be accessed.");
            return;
        }

        const type = voice.mimeType();
        voice.chunks = [];
        voice.recorder = type ? new MediaRecorder(voice.stream, { mimeType: type }) : new MediaRecorder(voice.stream);
        voice.recorder.ondataavailable = (e) => { if (e.data?.size) voice.chunks.push(e.data); };
        voice.recorder.onstop = () => voice.transcribe();
        voice.recorder.start();

        voice.started = Date.now();
        voice.setState("listening", "Listening… tap stop when you're done");
        const timerEl = $("voiceTimer");
        timerEl.textContent = "0:00";
        voice.timer = setInterval(() => {
            const s = Math.floor((Date.now() - voice.started) / 1000);
            timerEl.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
            if (s >= 120) voice.stop();           // keep clips small
        }, 250);
    },

    stop() {
        clearInterval(voice.timer);
        if (voice.recorder?.state !== "inactive") voice.recorder?.stop();
        voice.stream?.getTracks().forEach((t) => t.stop());
        voice.stream = null;
    },

    async transcribe() {
        const type = voice.recorder?.mimeType || "audio/webm";
        const blob = new Blob(voice.chunks, { type });
        voice.recorder = null;
        voice.chunks = [];

        if (blob.size < 1200) {
            voice.setState("idle");
            Toast.info("That recording was too short. Try again.");
            return;
        }

        voice.setState("transcribing", "Transcribing…");
        const extension = type.includes("ogg") ? "ogg" : type.includes("mp4") ? "mp4" : "webm";
        const form = new FormData();
        form.append("file", blob, `voice-${Date.now()}.${extension}`);
        const language = Prefs.get("voice.language", "");
        const url = "/voice/transcribe" + (language ? `?language=${encodeURIComponent(language)}` : "");

        let text = "";
        try {
            const data = await Api.request(url, { method: "POST", body: form });
            text = (data?.text || "").trim();
            if (!text) Toast.info("No speech was detected. Please try again.");
        } catch (error) {
            Toast.error(error.message);
        }

        if (!text) {
            voice.setState("idle");
            return;
        }

        const input = $("userInput");
        input.value = input.value ? `${input.value.trim()} ${text}` : text;
        autoGrow();

        if (Prefs.get("voice.autoSend") === "true" && !attachments.pending()) {
            voice.setState("transcribing", "Sending…");
            setTimeout(() => {
                voice.setState("idle");
                sendMessage();
            }, 250);
        } else {
            voice.setState("idle");
            input.focus();
            Toast.success("Transcribed — review and press Enter to send.");
        }
    },
};


/* =====================================================
   AUTH-AWARE UI
   ===================================================== */

// A guest conversation open at sign-in; reopened once it is claimed.
let pendingGuestConversation = null;
let wasGuest = false;

function applyAuthState() {
    const loggedIn = Boolean(Auth.user);
    const signedOut = Auth.status === "signed-out";

    if (loggedIn && wasGuest && state.conversationId) {
        pendingGuestConversation = state.conversationId;
        state.conversationId = null;
    }
    wasGuest = signedOut;
    const input = $("userInput");
    input.placeholder = "Ask anything…";
    document.body.classList.toggle("logged-out", signedOut);

    // New visitors see the sign-in dialog once per browser session; closing
    // it (x) lets them chat as a guest.
    if (signedOut && !AuthModal.current) {
        let prompted = false;
        try { prompted = sessionStorage.getItem("cortexa-login-prompted") === "1"; } catch (_) { /* storage blocked */ }
        if (!prompted) {
            try { sessionStorage.setItem("cortexa-login-prompted", "1"); } catch (_) { /* storage blocked */ }
            AuthModal.open("login", { message: "Log in to upload PDFs and images and keep your chats, or close this to chat as a guest." });
        }
    }

    if (!loggedIn) {
        state.conversationId = null;
        state.conversations = [];
        state.attachments.forEach((a) => a.xhr?.abort());
        state.attachments = [];
        attachments.render();
        context.set([]);
        Media.cache.clear();
    }

    if (!state.streaming) chat.welcome();
    sidebar.load({ openLatest: false });
}


/* =====================================================
   INIT
   ===================================================== */

document.addEventListener("DOMContentLoaded", () => {
    const input = $("userInput");

    chat.welcome();
    sidebar.skeleton();

    $("composer").addEventListener("submit", (event) => {
        event.preventDefault();
        if (!state.streaming) sendMessage();
        else if (Date.now() - state.streamStartedAt > STOP_ARM_DELAY_MS) state.streaming.abort();
    });

    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            if (!state.streaming) sendMessage();
        }
    });
    input.addEventListener("input", autoGrow);

    $("voiceButton").addEventListener("click", () => {
        if ($("voiceButton").dataset.state === "listening") voice.stop();
        else voice.start();
    });
    voice.setState("idle");

    $("newChatButton").addEventListener("click", newChat);
    $("chatSearch").addEventListener("input", debounce(() => sidebar.render(), 150));

    $("chatList").addEventListener("click", (event) => {
        const remove = event.target.closest(".chat-delete");
        const item = event.target.closest(".chat-item[data-id]");
        if (!item) return;
        if (remove) deleteConversation(item.dataset.id);
        else openConversation(item.dataset.id);
    });
    $("chatList").addEventListener("keydown", (event) => {
        const item = event.target.closest(".chat-item[data-id]");
        if (item && (event.key === "Enter" || event.key === " ")) {
            event.preventDefault();
            openConversation(item.dataset.id);
        }
    });

    const sidebarEl = $("sidebar");
    const overlay = $("overlay");
    const menu = $("menuButton");
    menu.addEventListener("click", () => {
        if (window.matchMedia("(max-width: 860px)").matches) {
            const open = !sidebarEl.classList.contains("open");
            sidebarEl.classList.toggle("open", open);
            overlay.hidden = !open;
            menu.setAttribute("aria-expanded", String(open));
        } else {
            const collapsed = sidebarEl.classList.toggle("collapsed");
            menu.setAttribute("aria-expanded", String(!collapsed));
        }
    });
    overlay.addEventListener("click", () => {
        sidebarEl.classList.remove("open");
        overlay.hidden = true;
    });

    initAttachments();

    window.addEventListener("guestchats:claimed", async () => {
        await sidebar.load();
        const id = pendingGuestConversation;
        pendingGuestConversation = null;
        if (id && !state.streaming && !state.conversationId) openConversation(id);
    });

    Auth.onChange(applyAuthState);
});
