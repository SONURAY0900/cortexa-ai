/* =====================================================
   Agentic AI — chat page
   Depends on common.js (Auth, Api, Toast, Modal, el, …)
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

const PDF_STAGES = [
    { key: "upload", label: "Uploading" },
    { key: "extracting", label: "Extracting" },
    { key: "chunking", label: "Chunking" },
    { key: "embedding", label: "Embedding" },
    { key: "indexing", label: "Indexing" },
    { key: "done", label: "Ready" },
];

const IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp", "image/bmp"];

const state = {
    conversationId: null,
    conversations: [],
    attachment: null,          // { kind: "pdf"|"image", id, name, size, status }
    streaming: null,           // AbortController of the active request
    uploadXHR: null,
};

const $ = (id) => document.getElementById(id);


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
    let text = escapeHTML(source || "");

    text = text.replace(/```([\w+-]*)\n?([\s\S]*?)(```|$)/g, (_, lang, code) => {
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
        const ol = line.match(/^\s*\d+[.)]\s+(.*)/);
        if (ul || ol) {
            const tag = ul ? "ul" : "ol";
            if (list !== tag) { closeList(); out.push(`<${tag}>`); list = tag; }
            out.push(`<li>${renderInline((ul || ol)[1])}</li>`);
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

    return out.join("").replace(/@@BLOCK(\d+)@@/g, (_, i) => blocks[i]);
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
        chat.inner.replaceChildren(el("section", { class: "hero-empty" }, [
            el("div", { class: "hero-logo" }, [el("img", { src: "/frontend/assets/logo/logo.svg", width: "56", height: "56", alt: "" })]),
            el("h1", { text: loggedIn ? "What can I help you with?" : "Your agentic AI assistant" }),
            el("p", {
                text: loggedIn
                    ? "I pick the right tool for every question — web search, Wikipedia, research papers, your PDFs or images."
                    : "Sign in to chat, search the web and research papers, and ask questions about your own PDFs and images.",
            }),
            loggedIn
                ? el("div", { class: "chips" }, SUGGESTIONS.map((s, i) => el("button", {
                    class: "chip",
                    type: "button",
                    style: `animation-delay:${80 + i * 60}ms`,
                    onclick: () => sendMessage(s.text),
                }, [
                    el("span", { class: `dot route-dot route-${s.route}` }),
                    el("span", { class: "chip-text", text: s.text }),
                    el("em", { text: ROUTES[s.route].label }),
                ])))
                : el("div", { class: "hero-actions" }, [
                    el("button", { class: "btn btn-primary", type: "button", text: "Log in", onclick: () => AuthModal.open("login") }),
                    el("button", { class: "btn btn-outline", type: "button", text: "Create account", onclick: () => AuthModal.open("signup") }),
                ]),
        ]));
    },

    skeleton() {
        chat.inner.replaceChildren(...[0, 1, 2].map((i) => el("div", { class: `message ${i % 2 ? "bot-message" : "user-message"} skeleton-message` }, [
            el("div", { class: "avatar skeleton" }),
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

    addUser(text, { time, attachment } = {}) {
        chat.removeHero();
        const content = el("div", { class: "message-content" }, [
            attachment ? el("div", { class: "attachment-tag", text: `${attachment.kind === "pdf" ? "📄" : "🖼️"} ${attachment.name}` }) : null,
            el("div", { class: "message-text", text }),
            el("div", { class: "message-meta", text: chat.timeLabel(time) }),
        ]);
        chat.inner.append(el("div", { class: "message user-message" }, [content]));
        chat.scroll(true);
    },

    /** Assistant card used both for history and live streaming. */
    addAssistant({ route = "", time } = {}) {
        chat.removeHero();
        const badge = el("span", { class: "route-badge", hidden: true });
        const head = el("div", { class: "answer-head" }, [
            el("strong", { text: "Assistant" }),
            badge,
            el("span", { class: "message-time", text: chat.timeLabel(time) }),
        ]);
        const body = el("div", { class: "answer-body" });
        const card = el("div", { class: "answer-card" }, [head, body]);
        const wrap = el("div", { class: "message bot-message" }, [
            el("div", { class: "avatar bot-avatar", "aria-hidden": "true", html: '<img src="/frontend/assets/logo/logo.svg" width="20" height="20" alt="">' }),
            el("div", { class: "message-content" }, [card]),
        ]);
        chat.inner.append(wrap);

        const api = {
            wrap, card, body, text: "",
            setRoute(name) {
                if (!ROUTES[name]) return;
                card.className = `answer-card route-${name}`;
                badge.className = `route-badge route-${name}`;
                badge.textContent = ROUTES[name].label;
                badge.hidden = false;
            },
            setText(markdown) {
                api.text = markdown;
                body.innerHTML = renderMarkdown(markdown);
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
                copy.addEventListener("click", () => copyText(api.text, copy));
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
                    el("span", { class: "source-title", text: s.title || "PDF excerpt" }),
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
    const status = el("span", { class: "thinking-label", text: "Thinking" });
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
    const wrap = el("div", { class: "message bot-message thinking" }, [
        el("div", { class: "avatar bot-avatar", "aria-hidden": "true", html: '<img src="/frontend/assets/logo/logo.svg" width="20" height="20" alt="">' }),
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
        setStatus(text) { status.textContent = text; },
        setRoute(route) {
            clearInterval(scanner);
            panel.classList.add("resolved");
            chips.forEach((c) => {
                c.classList.remove("scanning");
                c.classList.toggle("selected", c.dataset.route === route);
                c.classList.toggle("faded", c.dataset.route !== route);
            });
            status.textContent = ROUTES[route]?.status || "Working on it";
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

        if (!Auth.user) {
            list.replaceChildren(el("li", { class: "empty-note", text: "Log in to see your chats." }));
            return;
        }
        if (!items.length) {
            list.replaceChildren(el("li", { class: "empty-note", text: needle ? "No chats match your search." : "No chats yet. Ask your first question!" }));
            return;
        }

        list.replaceChildren(...items.map((c) => {
            const item = el("li", {
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
            ]);
            return item;
        }));
    },

    async load({ openLatest = false } = {}) {
        if (!Auth.user) {
            state.conversations = [];
            sidebar.render();
            return;
        }
        sidebar.skeleton();
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
        for (const m of data.messages || []) {
            const role = String(m.role || "").toUpperCase();
            if (role === "USER") {
                chat.addUser(m.content || "", { time: m.created_at });
            } else if (role === "ASSISTANT") {
                const card = chat.addAssistant({ route: m.route, time: m.created_at });
                card.setText(m.content || "");
                card.setSources(m.metadata?.sources || []);
                card.finish(m.route);
            }
        }
        if (!chat.inner.children.length) chat.welcome();
        chat.scroll(true);
    } catch (error) {
        chat.inner.replaceChildren();
        chat.addAssistant().setError(error.message, () => openConversation(id));
    }
}

async function newChat() {
    if (state.streaming) return;
    state.conversationId = null;
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
   SENDING / STREAMING
   ===================================================== */

function setStreaming(controller) {
    state.streaming = controller;
    const send = $("sendButton");
    send.classList.toggle("is-stop", Boolean(controller));
    send.setAttribute("aria-label", controller ? "Stop generating" : "Send message");
    send.title = controller ? "Stop generating" : "Send";
    $("attachFileButton").disabled = Boolean(controller) || Boolean(state.uploadXHR);
}

async function ensureConversation(firstMessage) {
    if (state.conversationId) return state.conversationId;
    const data = await Api.post("/conversations", { title: firstMessage.slice(0, 60) || "New Chat" });
    state.conversationId = data.conversation.id;
    state.conversations = [data.conversation, ...state.conversations];
    sidebar.render();
    return state.conversationId;
}

async function sendMessage(text) {
    const input = $("userInput");
    const query = (text ?? input.value).trim();

    if (!query || state.streaming) return;

    if (!Auth.user) {
        AuthModal.open("login", { message: "Please log in to start chatting." });
        return;
    }
    if (state.attachment && state.attachment.status !== "ready") {
        Toast.info("Please wait until the file has finished processing.");
        return;
    }

    input.value = "";
    autoGrow();

    const attachment = state.attachment ? { ...state.attachment } : null;
    chat.addUser(query, { attachment });

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
                document_id: attachment?.kind === "pdf" ? attachment.id : null,
                image_id: attachment?.kind === "image" ? attachment.id : null,
            },
        });

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
            if (card) {
                card.body.classList.remove("streaming");
                card.setText(full + "\n\n*Stopped.*");
                card.finish(route);
            }
        } else {
            if (!card) card = chat.addAssistant();
            card.body.classList.remove("streaming");
            card.setError(error.message || MESSAGES.generic, () => sendMessage(query));
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
   FILE UPLOAD (PDF stages are real backend stages)
   ===================================================== */

function limits() {
    return Auth.config?.limits || { pdf_mb: 25, image_mb: 10, audio_mb: 25 };
}

function renderAttachment() {
    const box = $("attachment");
    const a = state.attachment;
    if (!a) {
        box.hidden = true;
        box.replaceChildren();
        return;
    }
    box.hidden = false;

    const icon = a.kind === "pdf" ? "📄" : "🖼️";
    const remove = el("button", {
        class: "attachment-remove",
        type: "button",
        title: a.status === "ready" || a.status === "error" ? "Remove file" : "Cancel upload",
        "aria-label": "Remove file",
        html: ICONS.close,
        onclick: clearAttachment,
    });

    let detail;
    if (a.status === "error") {
        detail = el("div", { class: "attachment-status error", text: a.error });
    } else if (a.status === "ready") {
        detail = el("div", { class: "attachment-status ready" }, [
            el("span", { html: ICONS.check }),
            a.kind === "pdf" ? `Ready · ask questions about this PDF${a.chunks ? ` (${a.chunks} passages indexed)` : ""}` : "Ready · your next message will analyze this image",
        ]);
    } else if (a.kind === "pdf") {
        const current = PDF_STAGES.findIndex((s) => s.key === a.stage);
        detail = el("div", { class: "stepper" }, PDF_STAGES.map((s, i) => el("span", {
            class: "step" + (i < current ? " done" : i === current ? " active" : ""),
        }, [
            el("i"),
            s.key === "upload" && i === current && a.progress != null ? `Uploading ${a.progress}%` : s.label,
        ])));
    } else {
        detail = el("div", { class: "attachment-status" }, [
            el("span", { class: "progress-track" }, [el("span", { class: "progress-fill", style: `width:${a.progress || 0}%` })]),
            `Uploading ${a.progress || 0}%`,
        ]);
    }

    box.className = `attachment ${a.status}`;
    box.replaceChildren(
        el("span", { class: "attachment-icon", text: icon }),
        el("div", { class: "attachment-body" }, [
            el("div", { class: "attachment-name" }, [
                el("span", { class: "name", text: a.name }),
                el("span", { class: "size", text: formatBytes(a.size) }),
            ]),
            detail,
        ]),
        remove,
    );
}

function clearAttachment() {
    if (state.uploadXHR) {
        state.uploadXHR.abort();
        state.uploadXHR = null;
    }
    state.attachment = null;
    $("fileInput").value = "";
    $("attachFileButton").disabled = Boolean(state.streaming);
    renderAttachment();
}

function validateFile(file) {
    const name = file.name.toLowerCase();
    const isPDF = name.endsWith(".pdf") || file.type === "application/pdf";
    const isImage = IMAGE_TYPES.includes(file.type) || /\.(jpe?g|png|webp|bmp)$/.test(name);
    const { pdf_mb, image_mb } = limits();

    if (!isPDF && !isImage) return { error: "Unsupported file type. Please choose a PDF, JPG, PNG, WEBP or BMP file." };
    if (isPDF && file.size > pdf_mb * 1024 * 1024) return { error: `This PDF is larger than ${pdf_mb} MB.` };
    if (isImage && file.size > image_mb * 1024 * 1024) return { error: `This image is larger than ${image_mb} MB.` };
    if (!file.size) return { error: "This file is empty." };
    return { kind: isPDF ? "pdf" : "image" };
}

async function uploadFile(file) {
    if (!Auth.user) {
        AuthModal.open("login", { message: "Please log in to upload files." });
        return;
    }
    if (state.streaming) return;

    const check = validateFile(file);
    if (check.error) {
        Toast.error(check.error);
        return;
    }

    clearAttachment();
    state.attachment = { kind: check.kind, name: file.name, size: file.size, status: "uploading", stage: "upload", progress: 0 };
    renderAttachment();
    $("attachFileButton").disabled = true;

    const token = await Auth.token();
    const xhr = new XMLHttpRequest();
    state.uploadXHR = xhr;
    const url = check.kind === "pdf" ? "/upload/pdf?stream=true" : "/upload/image";
    xhr.open("POST", url);
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

    const current = state.attachment;
    const update = (patch) => {
        if (state.attachment !== current) return;   // superseded or cancelled
        Object.assign(current, patch);
        renderAttachment();
    };
    const fail = (message) => {
        update({ status: "error", error: message });
        state.uploadXHR = null;
        $("attachFileButton").disabled = Boolean(state.streaming);
    };

    xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) update({ progress: Math.round((event.loaded / event.total) * 100) });
    };
    xhr.upload.onload = () => update({ progress: 100 });

    let consumed = 0;
    const readEvents = () => {
        const text = xhr.responseText;
        const lines = text.slice(consumed).split("\n");
        const complete = text.endsWith("\n") ? lines : lines.slice(0, -1);
        for (const line of complete) {
            consumed += line.length + 1;
            if (!line.trim()) continue;
            let data;
            try { data = JSON.parse(line); } catch (_) { continue; }
            if (data.type === "stage") update({ stage: data.stage === "uploaded" ? "extracting" : data.stage, chunks: data.chunks ?? current.chunks });
            if (data.type === "done") {
                update({ status: "ready", stage: "done", id: data.document_id });
                Toast.success(`${current.name} is ready.`);
            }
            if (data.type === "error") fail(data.detail || "We couldn't process this PDF. Please try another file.");
        }
    };

    xhr.onprogress = () => { if (check.kind === "pdf" && xhr.status === 200) readEvents(); };

    xhr.onload = () => {
        state.uploadXHR = null;
        $("attachFileButton").disabled = Boolean(state.streaming);

        if (xhr.status === 401) {
            fail(MESSAGES.session);
            AuthModal.open("login");
            return;
        }
        if (xhr.status !== 200) {
            let detail = null;
            try { detail = JSON.parse(xhr.responseText).detail; } catch (_) { /* not JSON */ }
            fail(typeof detail === "string" ? detail : (check.kind === "pdf"
                ? "We couldn't process this PDF. Please try another file."
                : "We couldn't upload this image. Please try another file."));
            return;
        }
        if (check.kind === "pdf") {
            readEvents();
            if (current.status === "uploading") fail("The upload ended unexpectedly. Please try again.");
        } else {
            try {
                const data = JSON.parse(xhr.responseText);
                update({ status: "ready", id: data.image_id });
            } catch (_) {
                fail("We couldn't upload this image. Please try another file.");
            }
        }
    };
    xhr.onerror = () => fail(MESSAGES.network);
    xhr.onabort = () => { state.uploadXHR = null; };

    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
}

function initUpload() {
    const input = $("fileInput");
    $("attachFileButton").addEventListener("click", () => {
        if (!Auth.user) {
            AuthModal.open("login", { message: "Please log in to upload files." });
            return;
        }
        input.click();
    });
    input.addEventListener("change", () => {
        const file = input.files?.[0];
        if (file) uploadFile(file);
        input.value = "";
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
        const file = e.dataTransfer.files?.[0];
        if (file) uploadFile(file);
    });
}


/* =====================================================
   VOICE INPUT (Listening → Transcribing → Done)
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
        if (!Auth.user) {
            AuthModal.open("login", { message: "Please log in to use voice input." });
            return;
        }
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

        try {
            const data = await Api.request("/voice/transcribe", { method: "POST", body: form });
            const text = (data?.text || "").trim();
            if (!text) {
                Toast.info("No speech was detected. Please try again.");
            } else {
                const input = $("userInput");
                input.value = input.value ? `${input.value.trim()} ${text}` : text;
                autoGrow();
                input.focus();
                Toast.success("Transcribed — review and press Enter to send.");
            }
        } catch (error) {
            Toast.error(error.message);
        } finally {
            voice.setState("idle");
        }
    },
};


/* =====================================================
   AUTH-AWARE UI
   ===================================================== */

function applyAuthState() {
    const loggedIn = Boolean(Auth.user);
    const input = $("userInput");
    input.placeholder = loggedIn ? "Ask anything…" : "Log in to start chatting";
    document.body.classList.toggle("logged-out", !loggedIn);

    if (!loggedIn) {
        state.conversationId = null;
        state.conversations = [];
        clearAttachment();
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
        if (state.streaming) state.streaming.abort();
        else sendMessage();
    });

    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            if (!state.streaming) sendMessage();
        }
    });
    input.addEventListener("input", autoGrow);
    input.addEventListener("focus", () => {
        if (!Auth.user && Auth.resolved) AuthModal.open("login", { message: "Please log in to start chatting." });
    });

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

    initUpload();

    Auth.onChange(applyAuthState);
});
