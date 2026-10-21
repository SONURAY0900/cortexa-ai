/* =====================================================
   Cortexa AI — admin panel
   Depends on common.js. Every request goes to /admin/*,
   which the backend authorizes on each call.
   ===================================================== */

"use strict";

const view = () => document.getElementById("adminView");
const gate = () => document.getElementById("adminGate");

const ROUTE_LABELS = { DIRECT: "Direct", WIKIPEDIA: "Wikipedia", TAVILY: "Web", ARXIV: "arXiv", PDF_RAG: "PDF", IMAGE: "Image" };
const TYPE_LABELS = { CHAT: "Chat", SEARCH: "Search", PDF_UPLOAD: "PDF upload", IMAGE_UPLOAD: "Image upload", VOICE: "Voice", ERROR: "Error" };

const SVG = {
    users: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>',
    active: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>',
    search: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    pdf: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>',
    image: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>',
    docs: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V2H6.5A2.5 2.5 0 0 0 4 4.5z"/></svg>',
    chat: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
    error: ICONS.alert,
    trash: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>',
    eye: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>',
    refresh: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15"/></svg>',
};


/* =====================================================
   GATE (auth / authorization)
   ===================================================== */

function showGate(kind) {
    view().hidden = true;
    document.getElementById("adminNav").hidden = true;
    gate().hidden = false;

    const card = el("div", { class: "gate-card" });
    if (kind === "checking") {
        card.append(el("div", { class: "spinner big" }), el("p", { text: "Checking your access…" }));
    } else if (kind === "login") {
        card.append(
            el("div", { class: "gate-icon", html: ICONS.shield }),
            el("h1", { text: "Admin sign-in" }),
            el("p", { text: "Sign in with your administrator account to continue." }),
            el("button", { class: "btn btn-primary", type: "button", text: "Log in", onclick: () => AuthModal.open("login") }),
        );
    } else if (kind === "unavailable") {
        card.append(
            el("div", { class: "gate-icon", html: ICONS.info }),
            el("h1", { text: "Can't check your access right now" }),
            el("p", { text: "The server couldn't verify your session. Your account is fine; please try again." }),
            el("button", { class: "btn btn-primary", type: "button", text: "Try again", onclick: () => (Auth.status === "error" ? Auth.retry() : checkAccess()) }),
        );
    } else {
        card.append(
            el("div", { class: "gate-icon danger", html: ICONS.alert }),
            el("h1", { text: "Access denied" }),
            el("p", { text: MESSAGES.forbidden }),
            el("a", { class: "btn btn-outline", href: "/", text: "Back to chat" }),
        );
    }
    gate().replaceChildren(card);
}

async function checkAccess() {
    if (!Auth.resolved) return showGate("checking");
    if (Auth.status === "error") return showGate("unavailable");
    if (!Auth.user) return showGate("login");
    showGate("checking");
    try {
        await Api.get("/admin/me");
        gate().hidden = true;
        document.getElementById("adminNav").hidden = false;
        view().hidden = false;
        route();
    } catch (error) {
        showGate(error.status === 401 ? "login" : error.status === 403 ? "forbidden" : "unavailable");
    }
}


/* =====================================================
   SHARED UI
   ===================================================== */

function pageHead(title, subtitle, actions = []) {
    return el("div", { class: "page-head" }, [
        el("div", {}, [el("h1", { text: title }), subtitle ? el("p", { text: subtitle }) : null]),
        el("div", { class: "page-actions" }, actions),
    ]);
}

function notice(html) {
    return el("div", { class: "notice" }, [el("span", { html: ICONS.alert }), el("div", { html })]);
}

function statusPill(status) {
    const s = String(status || "").toUpperCase();
    const cls = s === "SUCCESS" || s === "COMPLETED" || s === "ACTIVE" ? "pill-success"
        : s === "FAILED" || s === "BANNED" ? "pill-danger"
            : s === "PENDING" || s === "PROCESSING" || s === "UNCONFIRMED" ? "pill-warning" : "";
    return el("span", { class: `pill ${cls}`, text: s ? s.charAt(0) + s.slice(1).toLowerCase() : "—" });
}

function routeBadge(route) {
    return route && ROUTE_LABELS[route]
        ? el("span", { class: `route-badge route-${route}`, text: ROUTE_LABELS[route] })
        : el("span", { class: "cell-sub", text: "—" });
}

function userCell(email, id) {
    return el("div", {}, [
        el("div", { class: "cell-main", text: email || (id ? "Deleted user" : "No owner") }),
        id ? el("div", { class: "cell-sub mono", text: id.slice(0, 8) + "…" }) : null,
    ]);
}

function refreshButton(onclick) {
    return el("button", { class: "btn btn-outline btn-sm", type: "button", onclick }, [el("span", { html: SVG.refresh }), "Refresh"]);
}

/**
 * Generic data table: filters → fetch → skeleton → rows/empty/error → pager.
 */
function dataTable({ endpoint, columns, filters = [], pageSize = 25, emptyTitle = "Nothing here yet", emptyText = "", onLoaded }) {
    const state = { page: 1, params: {} };
    const card = el("div", { class: "table-card" });
    const toolbar = el("div", { class: "toolbar" });
    let controller = null;

    for (const f of filters) {
        let input;
        if (f.type === "search") {
            input = el("input", { type: "search", placeholder: f.placeholder || "Search…", "aria-label": f.placeholder || "Search" });
            input.addEventListener("input", debounce(() => { state.params[f.name] = input.value.trim(); state.page = 1; load(); }, 350));
            toolbar.append(el("label", { class: "search" }, [el("span", { html: SVG.search }), input]));
        } else if (f.type === "select") {
            input = el("select", { class: "select", "aria-label": f.label }, f.options.map(([value, label]) => el("option", { value, text: label })));
            if (f.value) { input.value = f.value; state.params[f.name] = f.value; }
            input.addEventListener("change", () => { state.params[f.name] = input.value; state.page = 1; load(); });
            toolbar.append(input);
        } else if (f.type === "date") {
            input = el("input", { class: "input", type: "date", "aria-label": f.label });
            input.addEventListener("change", () => { state.params[f.name] = input.value; state.page = 1; load(); });
            toolbar.append(el("label", { class: "date" }, [f.label, input]));
        }
    }

    const table = el("table", { class: "data-table responsive" });
    const thead = el("thead", {}, [el("tr", {}, columns.map((c) => el("th", { class: c.num ? "num" : "", text: c.label })))]);
    const tbody = el("tbody");
    table.append(thead, tbody);
    const pager = el("div", { class: "pager" });
    card.append(el("div", { class: "table-scroll" }, [table]), pager);

    function skeleton() {
        tbody.replaceChildren(...Array.from({ length: 6 }, () => el("tr", {}, columns.map(() =>
            el("td", {}, [el("span", { class: "skeleton skeleton-line", style: `width:${40 + Math.random() * 50}%` })])))));
        pager.replaceChildren();
    }

    async function load() {
        controller?.abort();
        controller = new AbortController();
        skeleton();
        const query = new URLSearchParams({ page: state.page, page_size: pageSize });
        for (const [k, v] of Object.entries(state.params)) if (v) query.set(k, v);
        try {
            const data = await Api.get(`${endpoint}?${query}`, { signal: controller.signal });
            onLoaded?.(data);
            render(data);
        } catch (error) {
            if (error.name === "AbortError") return;
            tbody.replaceChildren(el("tr", {}, [el("td", { colspan: columns.length }, [
                el("div", { class: "table-error" }, [
                    el("span", { text: error.message }),
                    el("button", { class: "btn btn-outline btn-sm", type: "button", text: "Try again", onclick: load }),
                ]),
            ])]));
        }
    }

    function render(data) {
        const items = data.items || [];
        if (!items.length) {
            tbody.replaceChildren(el("tr", {}, [el("td", { colspan: columns.length }, [
                el("div", { class: "table-empty" }, [el("strong", { text: emptyTitle }), emptyText]),
            ])]));
        } else {
            tbody.replaceChildren(...items.map((item, i) => el("tr", { style: `animation-delay:${Math.min(i * 25, 300)}ms` },
                columns.map((c) => {
                    const value = c.render ? c.render(item, { reload: load }) : item[c.key];
                    return el("td", { class: c.num ? "num" : "", "data-label": c.label },
                        value instanceof Node ? [value] : [String(value ?? "—")]);
                }))));
        }

        const from = data.total ? (data.page - 1) * data.page_size + 1 : 0;
        const to = Math.min(data.page * data.page_size, data.total);
        pager.replaceChildren(
            el("span", { text: data.total ? `${from}–${to} of ${data.total}` : "0 results" }),
            el("div", { class: "pager-buttons" }, [
                el("button", { class: "btn btn-outline btn-sm", type: "button", text: "Previous", disabled: data.page <= 1 ? true : null, onclick: () => { state.page--; load(); } }),
                el("span", { text: `Page ${data.page} / ${data.pages}` }),
                el("button", { class: "btn btn-outline btn-sm", type: "button", text: "Next", disabled: data.page >= data.pages ? true : null, onclick: () => { state.page++; load(); } }),
            ]),
        );
    }

    load();
    return { node: el("div", {}, [filters.length ? toolbar : null, card]), reload: load };
}

function deleteAction({ title, message, details, confirmText, requireText, endpoint, json, success, reload }) {
    return el("button", {
        class: "icon-action danger", type: "button", title: confirmText, "aria-label": confirmText, html: SVG.trash,
        onclick: async (event) => {
            const button = event.currentTarget;
            const confirmed = await Modal.confirm({ title, message, details, confirmText, danger: true, requireText });
            if (!confirmed) return;
            button.disabled = true;
            try {
                await Api.del(endpoint, json);
                Toast.success(success);
                reload();
            } catch (error) {
                Toast.error(error.message);
                button.disabled = false;
            }
        },
    });
}


/* =====================================================
   VIEWS
   ===================================================== */

const views = {

    async dashboard() {
        const cards = el("div", { class: "stats" }, Array.from({ length: 8 }, () =>
            el("div", { class: "stat" }, [
                el("span", { class: "skeleton skeleton-line", style: "width:50%" }),
                el("span", { class: "skeleton", style: "display:block;height:28px;width:40%;margin-top:14px" }),
            ])));
        const banner = el("div");
        const routes = el("div", { class: "panel" }, [el("div", { class: "panel-head" }, [el("h2", { text: "Questions by route" })]), el("div", { class: "panel-body" }, [el("span", { class: "skeleton skeleton-line" })])]);
        const recent = el("div", { class: "panel" }, [el("div", { class: "panel-head" }, [el("h2", { text: "Recent activity" }), el("a", { class: "link-btn", href: "#activity", text: "View all" })]), el("div", { class: "panel-body" }, [el("span", { class: "skeleton skeleton-line" })])]);
        const chartPanel = (title) => el("div", { class: "panel" }, [el("div", { class: "panel-head" }, [el("h2", { text: title })]), el("div", { class: "panel-body" }, [el("span", { class: "skeleton skeleton-line" })])]);
        const questionsTrend = chartPanel("Questions per day");
        const growthTrend = chartPanel("Sign-ups and uploads per day");

        view().replaceChildren(
            pageHead("Dashboard", "Live totals from the application database.", [refreshButton(() => views.dashboard())]),
            banner, cards,
            el("div", { class: "panels panels-even" }, [questionsTrend, growthTrend]),
            el("div", { class: "panels" }, [routes, recent]),
        );

        let data;
        try {
            data = await Api.get("/admin/dashboard");
        } catch (error) {
            cards.replaceChildren(el("div", { class: "table-error", style: "grid-column:1/-1" }, [error.message,
                el("button", { class: "btn btn-outline btn-sm", type: "button", text: "Try again", onclick: () => views.dashboard() })]));
            return;
        }

        const c = data.cards;
        const d = data.details;
        const items = [
            ["Total users", c.total_users, SVG.users, "Supabase Auth accounts"],
            ["Active users", c.active_users, SVG.active, `Signed in or used the app in ${d.active_window_days} days`],
            ["Total searches", c.total_searches, SVG.search, "Wikipedia, web and arXiv questions"],
            ["PDF uploads", c.total_pdf_uploads, SVG.pdf, "Owned by a user"],
            ["Image uploads", c.total_image_uploads, SVG.image, "Recorded from v1.1"],
            ["Total documents", c.total_documents, SVG.docs, `${d.unowned_documents} evaluation / unowned`],
            ["Chat messages", c.total_chat_messages, SVG.chat, `${d.user_questions} questions · ${d.unanswered_questions} unanswered`],
            ["Failed requests", c.failed_requests ?? "—", SVG.error, d.activity_logging_enabled ? "Logged since activity logging was enabled" : "Activity logging not enabled", true],
        ];
        cards.replaceChildren(...items.map(([label, value, icon, sub, warn], i) => el("div", { class: "stat", style: `animation-delay:${i * 50}ms` }, [
            el("div", { class: "stat-top" }, [el("span", { class: "stat-label", text: label }), el("span", { class: `stat-icon${warn ? " warn" : ""}`, html: icon })]),
            el("div", { class: "stat-value", text: typeof value === "number" ? value.toLocaleString() : value }),
            el("div", { class: "stat-sub", text: sub }),
        ])));

        if (!d.activity_logging_enabled) {
            banner.replaceChildren(notice("Activity logging is not enabled yet, so voice use and failed requests are not recorded. Run <code>supabase/migrations/20261008_activity_logging.sql</code> in the Supabase SQL editor. Chats, searches and PDF uploads are already shown from existing tables."));
        }

        const entries = Object.entries(d.routes).sort((a, b) => b[1] - a[1]);
        routes.querySelector(".panel-body").replaceChildren(entries.length
            ? Charts.createChart(Charts.parseChartSpec({
                type: "bar",
                labels: entries.map(([route]) => ROUTE_LABELS[route] || "Unknown"),
                series: [{ name: "Questions", data: entries.map(([, count]) => count) }],
            }), { hideTitle: true })
            : el("div", { class: "table-empty", text: "No questions yet." }));

        // Last 14 days (UTC), oldest first. A server without `trends` (an
        // older backend) gets a note instead of breaking the rest of the page.
        const t = data.trends;
        const days = (t?.days || []).map((day) => new Date(`${day}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" }));
        const trendChart = (panel, series) => {
            const spec = Charts.parseChartSpec({ type: "line", labels: days, series });
            panel.querySelector(".panel-body").replaceChildren(spec
                ? Charts.createChart(spec, { hideTitle: true })
                : el("div", { class: "table-empty", text: "Daily trends aren't available from the server yet." }));
        };
        trendChart(questionsTrend, [{ name: "Questions", data: t?.questions }]);
        trendChart(growthTrend, [{ name: "New users", data: t?.new_users }, { name: "Uploads", data: t?.uploads }]);

        const recentBody = recent.querySelector(".panel-body");
        recentBody.style.padding = "0";
        recentBody.replaceChildren(data.recent_activity.length ? el("div", { class: "table-scroll" }, [el("table", { class: "data-table responsive" }, [
            el("tbody", {}, data.recent_activity.map((e) => el("tr", {}, [
                el("td", { "data-label": "When" }, [el("span", { class: "cell-sub", text: formatDate(e.timestamp) })]),
                el("td", { "data-label": "Type" }, [el("span", { class: "pill", text: TYPE_LABELS[e.type] || e.type })]),
                el("td", { "data-label": "Activity" }, [el("div", { class: "cell-query", text: e.action })]),
                el("td", { "data-label": "Status" }, [statusPill(e.status)]),
            ]))),
        ])]) : el("div", { class: "table-empty", text: "No activity yet." }));
    },

    users() {
        const table = dataTable({
            endpoint: "/admin/users",
            pageSize: 20,
            emptyTitle: "No users match these filters",
            filters: [
                { type: "search", name: "search", placeholder: "Search by email or user ID" },
                { type: "select", name: "status", label: "Status", options: [["", "All statuses"], ["ACTIVE", "Active"], ["UNCONFIRMED", "Unconfirmed"], ["BANNED", "Banned"]] },
                { type: "select", name: "role", label: "Role", options: [["", "All roles"], ["ADMIN", "Admins"], ["USER", "Users"]] },
                { type: "select", name: "sort", label: "Sort by", value: "created_at", options: [["created_at", "Newest"], ["last_activity", "Last activity"], ["email", "Email"], ["searches", "Searches"], ["chats", "Chat requests"], ["uploads", "Uploads"]] },
                { type: "select", name: "order", label: "Order", value: "desc", options: [["desc", "Descending"], ["asc", "Ascending"]] },
            ],
            columns: [
                { label: "User", render: (u) => userCell(u.email, u.id) },
                { label: "Created", render: (u) => formatDate(u.created_at, false) },
                { label: "Last activity", render: (u) => formatDate(u.last_activity) },
                { label: "Searches", num: true, key: "total_searches" },
                { label: "Uploads", num: true, key: "total_uploads" },
                { label: "Chats", num: true, key: "total_chat_requests" },
                { label: "Status", render: (u) => statusPill(u.status) },
                { label: "Role", render: (u) => el("span", { class: `pill ${u.role === "ADMIN" ? "pill-accent" : ""}`, text: u.role === "ADMIN" ? "Admin" : "User" }) },
                {
                    label: "", render: (u, { reload }) => el("div", { class: "row-actions" }, [
                        el("button", { class: "icon-action", type: "button", title: "View details", "aria-label": "View details", html: SVG.eye, onclick: () => showUser(u.id) }),
                        u.role === "ADMIN" ? el("button", { class: "icon-action", type: "button", disabled: true, title: "Admin accounts cannot be deleted here", html: SVG.trash }) : deleteAction({
                            title: "Delete this user?",
                            message: `${u.email} will be permanently deleted. This cannot be undone.`,
                            details: [
                                `${u.total_chat_requests + u.total_searches} chat questions and their conversations`,
                                `${u.total_uploads} uploaded PDFs/images, their stored chunks and files`,
                                "The Supabase sign-in account",
                            ],
                            confirmText: "Delete user",
                            requireText: u.email,
                            endpoint: `/admin/users/${encodeURIComponent(u.id)}`,
                            json: { confirm_email: u.email },
                            success: "User and associated data deleted.",
                            reload,
                        }),
                    ]),
                },
            ],
        });
        view().replaceChildren(pageHead("Users", "Accounts from Supabase Auth with their recorded activity.", [refreshButton(table.reload)]), table.node);
    },

    activityView({ title, subtitle, endpoint, types }) {
        const filters = [
            { type: "search", name: "q", placeholder: "Search activity…" },
            { type: "search", name: "user", placeholder: "Filter by user email" },
        ];
        if (types) filters.push({ type: "select", name: "type", label: "Type", options: [["", "All types"], ...Object.entries(TYPE_LABELS)] });
        filters.push(
            { type: "select", name: "status", label: "Status", options: [["", "Any status"], ["SUCCESS", "Success"], ["FAILED", "Failed"], ...(types ? [["PENDING", "Pending"]] : [])] },
            { type: "date", name: "date_from", label: "From" },
            { type: "date", name: "date_to", label: "To" },
        );

        const banner = el("div");
        const table = dataTable({
            endpoint,
            filters,
            emptyTitle: "No matching activity",
            emptyText: "Only activity the application records is shown.",
            onLoaded: (data) => banner.replaceChildren(data.activity_logging_enabled ? "" : notice(
                "Voice and failure events need activity logging. Run <code>supabase/migrations/20261008_activity_logging.sql</code> in the Supabase SQL editor; earlier events of those types were never recorded.")),
            columns: [
                { label: "Time", render: (e) => el("span", { class: "cell-sub", text: formatDate(e.timestamp) }) },
                { label: "User", render: (e) => userCell(e.user_email, e.user_id) },
                { label: "Type", render: (e) => el("span", { class: "pill", text: TYPE_LABELS[e.type] || e.type }) },
                { label: "Query / action", render: (e) => el("div", { class: "cell-query", text: e.action, title: e.action }) },
                { label: "Route", render: (e) => routeBadge(e.route) },
                { label: "Status", render: (e) => el("div", {}, [statusPill(e.status), e.status_note ? el("div", { class: "cell-sub", text: e.status_note }) : null]) },
                { label: "Document", render: (e) => el("span", { class: "cell-sub", text: e.source_name || "—" }) },
                {
                    label: "", render: (e, { reload }) => e.deletable ? deleteAction({
                        title: "Delete this activity record?",
                        message: "The log entry will be permanently removed.",
                        confirmText: "Delete record",
                        endpoint: `/admin/activity/${encodeURIComponent(e.id)}`,
                        success: "Activity record deleted.",
                        reload,
                    }) : el("span"),
                },
            ],
        });
        view().replaceChildren(pageHead(title, subtitle, [refreshButton(table.reload)]), banner, table.node);
    },

    searches() {
        views.activityView({ title: "Searches", subtitle: "Questions answered with Wikipedia, live web or arXiv.", endpoint: "/admin/searches" });
    },

    chats() {
        views.activityView({ title: "Chat activity", subtitle: "Every question users asked, with the route that answered it.", endpoint: "/admin/chat-activity" });
    },

    activity() {
        views.activityView({ title: "All activity", subtitle: "Chats, searches, uploads, voice and errors in one timeline.", endpoint: "/admin/activity", types: true });
    },

    documents() {
        const table = dataTable({
            endpoint: "/admin/documents",
            emptyTitle: "No documents found",
            filters: [
                { type: "search", name: "search", placeholder: "Search file name, owner or ID" },
                { type: "select", name: "owner", label: "Owner", options: [["", "All documents"], ["owned", "Uploaded by users"], ["unowned", "Evaluation / unowned"]] },
                { type: "select", name: "status", label: "Status", options: [["", "Any status"], ["COMPLETED", "Completed"], ["PROCESSING", "Processing"]] },
            ],
            columns: [
                { label: "File", render: (d) => el("div", {}, [el("div", { class: "cell-main", text: d.filename }), el("div", { class: "cell-sub mono", text: d.id })]) },
                { label: "Owner", render: (d) => d.owned ? userCell(d.user_email, d.user_id) : el("span", { class: "pill pill-warning", text: "Evaluation / unowned" }) },
                { label: "Uploaded", render: (d) => formatDate(d.uploaded_at) },
                { label: "Status", render: (d) => statusPill(d.processing_status) },
                { label: "Chunks", num: true, key: "chunks" },
                { label: "Size", num: true, render: (d) => formatBytes(d.file_size) },
                {
                    label: "", render: (d, { reload }) => el("div", { class: "row-actions" }, [deleteAction({
                        title: "Are you sure you want to delete this document?",
                        message: `“${d.filename}” and its ${d.chunks} stored chunks will be permanently deleted, along with the uploaded file.`,
                        details: d.owned ? [] : ["This document has no owner. It is probably an evaluation copy; evaluation datasets that reference its ID will stop working."],
                        confirmText: "Delete document",
                        endpoint: `/admin/documents/${encodeURIComponent(d.id)}`,
                        success: "Document deleted.",
                        reload,
                    })]),
                },
            ],
        });
        view().replaceChildren(pageHead("Documents", "PDFs stored for retrieval. Page counts are not stored by the pipeline.", [refreshButton(table.reload)]), table.node);
    },

    images() {
        const table = dataTable({
            endpoint: "/admin/images",
            emptyTitle: "No image uploads recorded",
            emptyText: "Image uploads are recorded from v1.1 onward; earlier uploads were not stored.",
            filters: [{ type: "search", name: "search", placeholder: "Search file name or owner" }],
            columns: [
                { label: "File", render: (f) => el("div", {}, [el("div", { class: "cell-main", text: f.filename }), el("div", { class: "cell-sub", text: f.mime_type || "" })]) },
                { label: "Owner", render: (f) => userCell(f.user_email, f.user_id) },
                { label: "Uploaded", render: (f) => formatDate(f.uploaded_at) },
                { label: "OCR", render: (f) => statusPill(f.ocr_status) },
                { label: "Vision", render: (f) => statusPill(f.vision_status) },
                { label: "Size", num: true, render: (f) => formatBytes(f.file_size) },
                {
                    label: "", render: (f, { reload }) => el("div", { class: "row-actions" }, [deleteAction({
                        title: "Delete this image?",
                        message: `“${f.filename}” and its OCR/vision results will be permanently deleted.`,
                        confirmText: "Delete image",
                        endpoint: `/admin/images/${encodeURIComponent(f.id)}`,
                        success: "Image deleted.",
                        reload,
                    })]),
                },
            ],
        });
        view().replaceChildren(pageHead("Images", "Images uploaded for OCR and vision analysis.", [refreshButton(table.reload)]), table.node);
    },

    audit() {
        const banner = el("div");
        const table = dataTable({
            endpoint: "/admin/audit-log",
            emptyTitle: "No admin actions recorded",
            onLoaded: (data) => banner.replaceChildren(data.enabled ? "" : notice("The audit log needs the activity logging migration: run <code>supabase/migrations/20261008_activity_logging.sql</code> in the Supabase SQL editor.")),
            columns: [
                { label: "Time", render: (a) => el("span", { class: "cell-sub", text: formatDate(a.created_at) }) },
                { label: "Admin", render: (a) => el("span", { class: "cell-main", text: a.admin_email }) },
                { label: "Action", render: (a) => el("span", { class: "pill", text: a.action.replace(/_/g, " ").toLowerCase() }) },
                { label: "Target", render: (a) => el("div", {}, [el("div", { text: a.target_type }), el("div", { class: "cell-sub mono", text: a.target_id })]) },
                { label: "Status", render: (a) => statusPill(a.status) },
                { label: "Details", render: (a) => el("div", { class: "cell-sub", text: Object.entries(a.details || {}).map(([k, v]) => `${k}: ${v}`).join(" · ") }) },
            ],
        });
        view().replaceChildren(pageHead("Audit log", "Every destructive admin action, successful or not.", [refreshButton(table.reload)]), banner, table.node);
    },

    async health() {
        const grid = el("div", { class: "health-grid" }, Array.from({ length: 6 }, () => el("div", { class: "health-card" }, [
            el("span", { class: "skeleton skeleton-line", style: "width:40%" }), el("span", { class: "skeleton skeleton-line" }), el("span", { class: "skeleton skeleton-line", style: "width:70%" }),
        ])));
        const meta = el("p");
        view().replaceChildren(pageHead("System health", "Live checks run by the backend. No secrets are shown.", [refreshButton(() => views.health())]), meta, grid);

        try {
            const data = await Api.get("/admin/system-health");
            meta.replaceChildren(
                el("span", { class: `pill ${data.overall === "ok" ? "pill-success" : "pill-warning"}`, text: data.overall === "ok" ? "All systems operational" : "Degraded" }),
                " ",
                el("span", { class: "pill", text: `Environment: ${data.environment}` }),
                " ",
                el("span", { class: "pill", text: `Version ${data.version}` }),
            );
            meta.style.marginBottom = "16px";
            const labels = { backend: "Backend", database: "Database", auth: "Authentication", llm: "AI / LLM (Groq)", rag: "RAG", storage: "Storage", activity_logging: "Activity logging" };
            grid.replaceChildren(...Object.entries(data.checks).map(([key, check], i) => el("div", { class: `health-card ${check.status}`, style: `animation-delay:${i * 50}ms` }, [
                el("h3", {}, [el("span", { class: "health-dot" }), labels[key] || key]),
                el("p", { text: check.detail }),
                el("div", { class: "latency", text: check.latency_ms ? `${check.latency_ms} ms` : "" }),
            ])));
        } catch (error) {
            grid.replaceChildren(el("div", { class: "table-error" }, [error.message]));
        }
    },

    async settings() {
        const box = el("div", { class: "panel" }, [el("div", { class: "panel-body" }, [el("span", { class: "skeleton skeleton-line" })])]);
        view().replaceChildren(pageHead("Settings", "Read-only configuration. Change these through environment variables on the server."), box);
        try {
            const s = await Api.get("/admin/settings");
            const rows = [
                ["Environment", s.environment],
                ["Version", s.version],
                ["Admin accounts", `${s.admin_accounts} (ADMIN_EMAIL + app_metadata.role)`],
                ["Allowed origins", s.allowed_origins.join(", ")],
                ["Upload limits", `PDF ${s.upload_limits_mb.pdf} MB · image ${s.upload_limits_mb.image} MB · audio ${s.upload_limits_mb.audio} MB`],
                ["Chat model", s.models.llm],
                ["Vision model", s.models.vision],
                ["Speech model", s.models.speech],
                ["Embedding model", s.models.embedding],
                ["Browser sign-in key", s.supabase_publishable_key_configured ? "Configured" : "Missing (SUPABASE_PUBLISHABLE_KEY)"],
            ];
            box.replaceChildren(el("div", { class: "kv" }, rows.flatMap(([k, v]) => [el("div", { text: k }), el("div", { text: v })])));
            view().append(el("div", { class: "panel", style: "margin-top:14px" }, [
                el("div", { class: "panel-head" }, [el("h2", { text: "Adding another admin" })]),
                el("div", { class: "panel-body" }, [el("p", { class: "modal-text", html: "Add the email to <code>ADMIN_EMAIL</code> (comma-separated), make sure the account exists and is confirmed, then run <code>python -m scripts.grant_admin</code> from <code>backend/</code>. Use <code>--revoke</code> to remove admin rights." })]),
            ]));
        } catch (error) {
            box.replaceChildren(el("div", { class: "table-error" }, [error.message]));
        }
    },
};

async function showUser(userId) {
    const body = el("div", {}, [el("div", { class: "spinner big", style: "margin:20px auto" })]);
    const modal = Modal.open([el("h2", { id: "userTitle", class: "modal-title", text: "User details" }), body], { wide: true, labelledBy: "userTitle" });
    try {
        const { user, recent_activity: activity } = await Api.get(`/admin/users/${encodeURIComponent(userId)}`);
        body.replaceChildren(
            el("p", { class: "modal-text" }, [el("strong", { text: user.email }), " · ", el("span", { class: "mono", text: user.id })]),
            el("div", { class: "detail-grid" }, [
                ["Status", statusPill(user.status)], ["Role", user.role === "ADMIN" ? "Admin" : "User"], ["Sign-in", user.provider],
                ["Created", formatDate(user.created_at)], ["Last sign-in", formatDate(user.last_sign_in_at)], ["Searches", user.total_searches],
                ["Chat requests", user.total_chat_requests], ["Uploads", user.total_uploads], ["Activity shown", activity.length],
            ].map(([k, v]) => el("div", {}, [el("span", { text: k }), v instanceof Node ? v : el("strong", { text: String(v) })]))),
            activity.length ? el("div", { class: "detail-list" }, [el("table", { class: "data-table responsive" }, [el("tbody", {}, activity.map((e) => el("tr", {}, [
                el("td", { "data-label": "When" }, [el("span", { class: "cell-sub", text: formatDate(e.timestamp) })]),
                el("td", { "data-label": "Type" }, [el("span", { class: "pill", text: TYPE_LABELS[e.type] || e.type })]),
                el("td", { "data-label": "Activity" }, [el("div", { class: "cell-query", text: e.action })]),
                el("td", { "data-label": "Status" }, [statusPill(e.status)]),
            ])))])]) : el("div", { class: "table-empty", text: "No recorded activity." }),
            el("div", { class: "modal-actions" }, [el("button", { class: "btn btn-outline", type: "button", text: "Close", onclick: () => modal.close() })]),
        );
    } catch (error) {
        body.replaceChildren(el("div", { class: "table-error" }, [error.message]));
    }
}


/* =====================================================
   ROUTING / NAV
   ===================================================== */

function route() {
    const name = (location.hash || "#dashboard").slice(1);
    const fn = views[name] && name !== "activityView" ? views[name] : views.dashboard;
    document.querySelectorAll(".admin-nav a").forEach((a) => {
        const active = a.dataset.view === name;
        a.classList.toggle("active", active);
        if (active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    document.getElementById("adminNav").classList.remove("open");
    document.getElementById("overlay").hidden = true;
    view().replaceChildren();
    view().style.animation = "none";
    void view().offsetWidth;          // restart the entrance animation
    view().style.animation = "";
    fn();
    document.getElementById("adminMain").scrollTo({ top: 0 });
}

document.addEventListener("DOMContentLoaded", () => {
    showGate("checking");

    window.addEventListener("hashchange", () => { if (!view().hidden) route(); });

    const nav = document.getElementById("adminNav");
    const overlay = document.getElementById("overlay");
    document.getElementById("menuButton").addEventListener("click", () => {
        const open = !nav.classList.contains("open");
        nav.classList.toggle("open", open);
        overlay.hidden = !open;
        document.getElementById("menuButton").setAttribute("aria-expanded", String(open));
    });
    overlay.addEventListener("click", () => { nav.classList.remove("open"); overlay.hidden = true; });
    document.getElementById("adminLogout").addEventListener("click", () => Auth.logout());

    Auth.onChange(() => checkAccess());
});
