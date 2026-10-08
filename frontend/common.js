/* =====================================================
   Agentic AI — shared frontend runtime (all pages)

   - Theme, animated background, backend status
   - Supabase Auth (config served by the backend)
   - API helper with friendly error messages
   - Toasts, confirm dialog, auth modal, user menu
   ===================================================== */

"use strict";

const SUPABASE_JS_URL =
    "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.117.3/dist/umd/supabase.js";
const SUPABASE_JS_SRI =
    "sha384-BWcjm9OdFth9TbhCxZPdm+gAOUMAzQy9nmTs12ioXdCcbVnJaPMn+fMUoQCLt60R";

const MESSAGES = {
    network: "Unable to connect to the server. Please try again.",
    session: "Your session has expired. Please log in again.",
    forbidden: "You do not have permission to access this page.",
    generic: "Something went wrong. Please try again.",
};


/* =====================================================
   SMALL HELPERS
   ===================================================== */

function escapeHTML(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (value === null || value === undefined || value === false) continue;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key === "html") node.innerHTML = value;
        else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of [].concat(children)) {
        if (child === null || child === undefined || child === false) continue;
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}

function debounce(fn, ms = 300) {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), ms);
    };
}

function safeUrl(url) {
    try {
        const parsed = new URL(url, window.location.origin);
        return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : null;
    } catch (_) {
        return null;
    }
}

function formatDate(value, withTime = true) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleString([], withTime
        ? { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
        : { year: "numeric", month: "short", day: "numeric" });
}

function formatBytes(bytes) {
    if (!bytes && bytes !== 0) return "—";
    const units = ["B", "KB", "MB", "GB"];
    let size = bytes, unit = 0;
    while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit++; }
    return `${size.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

const ICONS = {
    moon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
    check: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    alert: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>',
    info: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>',
    close: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>',
    shield: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
    logout: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg>',
};


/* =====================================================
   THEME
   ===================================================== */

const Theme = {
    get() {
        try { return localStorage.getItem("theme"); } catch (_) { return null; }
    },
    apply(theme) {
        document.documentElement.setAttribute("data-theme", theme);
        try { localStorage.setItem("theme", theme); } catch (_) { /* private mode */ }
    },
    init() {
        const preferred = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
        Theme.apply(Theme.get() || preferred);
        document.getElementById("themeToggle")?.addEventListener("click", () => {
            const current = document.documentElement.getAttribute("data-theme");
            Theme.apply(current === "dark" ? "light" : "dark");
        });
    },
};

Theme.apply(Theme.get() || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));


/* =====================================================
   ANIMATED BACKGROUND (paused when hidden; off for reduced motion)
   ===================================================== */

function initBackground() {
    const bg = el("div", { class: "bg", "aria-hidden": "true" });
    bg.innerHTML = '<span class="orb o1"></span><span class="orb o2"></span><span class="orb o3"></span><span class="bg-grid"></span><canvas id="bgCanvas"></canvas>';
    document.body.prepend(bg);

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const canvas = bg.querySelector("canvas");
    const ctx = canvas.getContext("2d");
    let w = 0, h = 0, points = [], color = "99,102,241", tick = 0;

    function resize() {
        w = canvas.width = window.innerWidth;
        h = canvas.height = window.innerHeight;
        const count = Math.min(window.innerWidth < 700 ? 28 : 60, Math.floor((w * h) / 22000));
        points = Array.from({ length: count }, () => ({
            x: Math.random() * w, y: Math.random() * h,
            vx: (Math.random() - 0.5) * 0.3, vy: (Math.random() - 0.5) * 0.3,
        }));
    }

    function frame() {
        if (!document.hidden) {
            if (tick++ % 60 === 0) {
                color = getComputedStyle(document.documentElement).getPropertyValue("--particle").trim() || color;
            }
            ctx.clearRect(0, 0, w, h);
            for (let i = 0; i < points.length; i++) {
                const p = points[i];
                p.x += p.vx; p.y += p.vy;
                if (p.x < 0 || p.x > w) p.vx *= -1;
                if (p.y < 0 || p.y > h) p.vy *= -1;
                ctx.beginPath();
                ctx.arc(p.x, p.y, 1.5, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(${color},0.5)`;
                ctx.fill();
                for (let j = i + 1; j < points.length; j++) {
                    const q = points[j];
                    const d = Math.hypot(p.x - q.x, p.y - q.y);
                    if (d < 120) {
                        ctx.beginPath();
                        ctx.moveTo(p.x, p.y);
                        ctx.lineTo(q.x, q.y);
                        ctx.strokeStyle = `rgba(${color},${0.14 * (1 - d / 120)})`;
                        ctx.lineWidth = 1;
                        ctx.stroke();
                    }
                }
            }
        }
        requestAnimationFrame(frame);
    }

    window.addEventListener("resize", debounce(resize, 150));
    resize();
    frame();
}


/* =====================================================
   TOASTS
   ===================================================== */

const Toast = {
    container: null,
    show(message, type = "info", timeout = 4200) {
        if (!Toast.container) {
            Toast.container = el("div", { class: "toast-stack", role: "status", "aria-live": "polite" });
            document.body.append(Toast.container);
        }
        const icon = type === "success" ? ICONS.check : type === "error" ? ICONS.alert : ICONS.info;
        const toast = el("div", { class: `toast toast-${type}` }, [
            el("span", { class: "toast-icon", html: icon }),
            el("span", { class: "toast-text", text: message }),
            el("button", { class: "toast-close", type: "button", "aria-label": "Dismiss", html: ICONS.close, onclick: () => dismiss() }),
        ]);
        const dismiss = () => {
            toast.classList.add("leaving");
            setTimeout(() => toast.remove(), 220);
        };
        Toast.container.append(toast);
        if (timeout) setTimeout(dismiss, timeout);
    },
    success: (m) => Toast.show(m, "success"),
    error: (m) => Toast.show(m, "error", 6000),
    info: (m) => Toast.show(m, "info"),
};


/* =====================================================
   MODALS / CONFIRM DIALOG
   ===================================================== */

const Modal = {
    open(content, { onClose, labelledBy, wide = false } = {}) {
        const backdrop = el("div", { class: "modal-backdrop" });
        const dialog = el("div", {
            class: `modal${wide ? " modal-wide" : ""}`,
            role: "dialog",
            "aria-modal": "true",
            "aria-labelledby": labelledBy || null,
        }, content);
        backdrop.append(dialog);
        document.body.append(backdrop);
        document.body.classList.add("modal-open");

        const previouslyFocused = document.activeElement;
        const close = () => {
            backdrop.classList.add("leaving");
            document.removeEventListener("keydown", onKey);
            setTimeout(() => {
                backdrop.remove();
                if (!document.querySelector(".modal-backdrop")) document.body.classList.remove("modal-open");
                previouslyFocused?.focus?.();
            }, 180);
            onClose?.();
        };
        const onKey = (event) => { if (event.key === "Escape") close(); };
        document.addEventListener("keydown", onKey);
        backdrop.addEventListener("mousedown", (event) => { if (event.target === backdrop) close(); });

        setTimeout(() => dialog.querySelector("input, button, textarea, select")?.focus(), 30);
        return { close, dialog };
    },

    /**
     * Resolves true only when the user explicitly confirms.
     * requireText: the user must type this exact value first.
     */
    confirm({ title, message, confirmText = "Confirm", danger = false, requireText = null, details = [] }) {
        return new Promise((resolve) => {
            let settled = false;
            const finish = (value) => {
                if (settled) return;
                settled = true;
                resolve(value);
            };

            const input = requireText ? el("input", {
                class: "input",
                type: "text",
                autocomplete: "off",
                placeholder: requireText,
                "aria-label": `Type ${requireText} to confirm`,
            }) : null;

            const confirmButton = el("button", {
                class: `btn ${danger ? "btn-danger" : "btn-primary"}`,
                type: "button",
                text: confirmText,
                disabled: requireText ? true : null,
            });

            input?.addEventListener("input", () => {
                confirmButton.disabled = input.value.trim().toLowerCase() !== requireText.toLowerCase();
            });

            const modal = Modal.open([
                el("div", { class: `modal-icon${danger ? " danger" : ""}`, html: danger ? ICONS.alert : ICONS.info }),
                el("h2", { id: "confirmTitle", class: "modal-title", text: title }),
                el("p", { class: "modal-text", text: message }),
                details.length ? el("ul", { class: "modal-list" }, details.map((d) => el("li", { text: d }))) : null,
                requireText ? el("label", { class: "field-label" }, [
                    "Type ", el("strong", { text: requireText }), " to confirm",
                ]) : null,
                input,
                el("div", { class: "modal-actions" }, [
                    el("button", { class: "btn btn-ghost", type: "button", text: "Cancel", onclick: () => modal.close() }),
                    confirmButton,
                ]),
            ], { onClose: () => finish(false), labelledBy: "confirmTitle" });

            confirmButton.addEventListener("click", () => {
                finish(true);
                modal.close();
            });
        });
    },
};


/* =====================================================
   SUPABASE AUTH
   ===================================================== */

const Auth = {
    client: null,
    session: null,
    user: null,
    isAdmin: false,
    ready: null,
    listeners: new Set(),
    config: null,

    onChange(fn) {
        Auth.listeners.add(fn);
        return () => Auth.listeners.delete(fn);
    },

    emit() {
        Auth.listeners.forEach((fn) => {
            try { fn(Auth.user); } catch (error) { console.error(error); }
        });
    },

    loadScript() {
        return new Promise((resolve, reject) => {
            if (window.supabase?.createClient) return resolve();
            const script = el("script", { src: SUPABASE_JS_URL, integrity: SUPABASE_JS_SRI, crossorigin: "anonymous" });
            script.onload = resolve;
            script.onerror = () => reject(new Error("Unable to load the sign-in service."));
            document.head.append(script);
        });
    },

    init() {
        Auth.ready = (async () => {
            try {
                const [config] = await Promise.all([
                    fetch("/config").then((r) => {
                        if (!r.ok) throw new Error("config");
                        return r.json();
                    }),
                    Auth.loadScript(),
                ]);
                Auth.config = config;

                if (!config.supabase_url || !config.supabase_publishable_key) {
                    throw new Error("Sign-in is not configured on the server.");
                }

                Auth.client = window.supabase.createClient(config.supabase_url, config.supabase_publishable_key);

                Auth.client.auth.onAuthStateChange((event, session) => {
                    if (event === "PASSWORD_RECOVERY") {
                        setTimeout(() => AuthModal.open("reset"), 50);
                    }
                    // Defer: Supabase recommends not awaiting inside this callback.
                    setTimeout(() => Auth.setSession(session), 0);
                });

                const { data } = await Auth.client.auth.getSession();
                await Auth.setSession(data.session);

                const params = new URLSearchParams(window.location.search);
                if (params.get("reset") === "1") {
                    window.history.replaceState({}, document.title, window.location.pathname);
                    AuthModal.open("reset");
                }
            } catch (error) {
                console.error("Auth init failed:", error);
                Toast.error(error.message === "config" ? MESSAGES.network : error.message);
                Auth.emit();
            }
        })();
        return Auth.ready;
    },

    async setSession(session) {
        const previousId = Auth.user?.id || null;
        Auth.session = session || null;
        Auth.user = session?.user || null;

        if (Auth.user && Auth.user.id !== previousId) {
            try {
                const me = await Api.get("/auth/me", { quiet: true });
                Auth.isAdmin = Boolean(me?.user?.is_admin);
            } catch (_) {
                Auth.isAdmin = false;
            }
        }
        if (!Auth.user) Auth.isAdmin = false;

        const firstResolution = !Auth.resolved;
        Auth.resolved = true;

        if (firstResolution || (Auth.user?.id || null) !== previousId) {
            UserMenu.render();
            Auth.emit();
        }
    },

    async token() {
        if (!Auth.client) return null;
        const { data } = await Auth.client.auth.getSession();
        Auth.session = data.session;
        return data.session?.access_token || null;
    },

    async logout() {
        try {
            await Auth.client?.auth.signOut();
        } catch (error) {
            console.error(error);
        }
        await Auth.setSession(null);
        Toast.info("You have been logged out.");
    },
};


/* =====================================================
   API
   ===================================================== */

class ApiError extends Error {
    constructor(message, status = 0) {
        super(message);
        this.status = status;
    }
}

const Api = {
    async request(path, { method = "GET", json, body, headers = {}, quiet = false, signal, raw = false } = {}) {
        const token = await Auth.token();
        const finalHeaders = { ...headers };
        if (token) finalHeaders.Authorization = `Bearer ${token}`;
        if (json !== undefined) finalHeaders["Content-Type"] = "application/json";

        let response;
        try {
            response = await fetch(path, {
                method,
                headers: finalHeaders,
                body: json !== undefined ? JSON.stringify(json) : body,
                signal,
            });
        } catch (error) {
            if (error.name === "AbortError") throw error;
            throw new ApiError(MESSAGES.network, 0);
        }

        if (raw && response.ok) return response;

        let data = null;
        try { data = await response.json(); } catch (_) { data = null; }

        if (!response.ok) {
            let message = data?.detail;
            if (typeof message !== "string") message = null;
            if (response.status === 401) {
                message = MESSAGES.session;
                if (!quiet && Auth.user) {
                    await Auth.setSession(null);
                    AuthModal.open("login");
                }
            } else if (response.status === 403 && !message) {
                message = MESSAGES.forbidden;
            } else if (response.status >= 500 && !message) {
                message = MESSAGES.generic;
            }
            throw new ApiError(message || MESSAGES.generic, response.status);
        }
        return data;
    },
    get: (path, options) => Api.request(path, { ...options, method: "GET" }),
    post: (path, json, options) => Api.request(path, { ...options, method: "POST", json }),
    del: (path, json, options) => Api.request(path, { ...options, method: "DELETE", json }),
};


/* =====================================================
   AUTH MODAL (login / sign up / forgot / reset)
   ===================================================== */

const AuthModal = {
    current: null,

    open(mode = "login", { message = "" } = {}) {
        AuthModal.current?.close();

        const state = { mode };
        const title = el("h2", { id: "authTitle", class: "modal-title" });
        const subtitle = el("p", { class: "modal-text" });
        const nameInput = el("input", { class: "input", type: "text", placeholder: "Full name", autocomplete: "name", "aria-label": "Full name" });
        const emailInput = el("input", { class: "input", type: "email", placeholder: "Email", autocomplete: "email", "aria-label": "Email", required: true });
        const passwordInput = el("input", { class: "input", type: "password", placeholder: "Password", autocomplete: "current-password", "aria-label": "Password", required: true });
        const confirmInput = el("input", { class: "input", type: "password", placeholder: "Confirm new password", autocomplete: "new-password", "aria-label": "Confirm new password" });
        const feedback = el("div", { class: "form-feedback", role: "alert" });
        const submit = el("button", { class: "btn btn-primary btn-block", type: "submit" });
        const google = el("button", { class: "btn btn-outline btn-block", type: "button" }, [
            el("span", { html: '<svg width="16" height="16" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>' }),
            "Continue with Google",
        ]);
        const forgot = el("button", { class: "link-btn", type: "button", text: "Forgot password?" });
        const switcher = el("button", { class: "link-btn", type: "button" });
        const divider = el("div", { class: "divider" }, [el("span", { text: "or" })]);

        const form = el("form", { class: "auth-form", novalidate: true }, [
            nameInput, emailInput, passwordInput, confirmInput, forgot, feedback, submit, divider, google, switcher,
        ]);

        const show = (node, visible) => { node.hidden = !visible; };
        const setFeedback = (text, type = "error") => {
            feedback.textContent = text || "";
            feedback.className = `form-feedback ${text ? type : ""}`;
        };

        const render = () => {
            const m = state.mode;
            title.textContent = { login: "Welcome back", signup: "Create your account", forgot: "Reset your password", reset: "Choose a new password" }[m];
            subtitle.textContent = {
                login: "Sign in to chat, upload PDFs and keep your history.",
                signup: "It only takes a moment.",
                forgot: "We'll email you a secure reset link.",
                reset: "Enter your new password twice.",
            }[m];
            show(nameInput, m === "signup");
            show(emailInput, m !== "reset");
            show(passwordInput, m !== "forgot");
            show(confirmInput, m === "reset");
            show(forgot, m === "login");
            show(google, m === "login" || m === "signup");
            show(divider, m === "login" || m === "signup");
            show(switcher, m !== "reset");
            passwordInput.placeholder = m === "reset" ? "New password" : "Password";
            passwordInput.autocomplete = m === "login" ? "current-password" : "new-password";
            submit.textContent = { login: "Log in", signup: "Sign up", forgot: "Send reset link", reset: "Update password" }[m];
            switcher.textContent = m === "login" ? "New here? Create an account" : m === "signup" ? "Already have an account? Log in" : "Back to log in";
            setFeedback(message, "info");
            message = "";
        };

        forgot.addEventListener("click", () => { state.mode = "forgot"; render(); emailInput.focus(); });
        switcher.addEventListener("click", () => {
            state.mode = state.mode === "login" ? "signup" : "login";
            render();
        });

        google.addEventListener("click", async () => {
            google.disabled = true;
            setFeedback("Opening Google sign-in…", "info");
            const { error } = await Auth.client.auth.signInWithOAuth({
                provider: "google",
                options: { redirectTo: window.location.origin + window.location.pathname },
            });
            if (error) {
                setFeedback(error.message || "Google sign-in failed.");
                google.disabled = false;
            }
        });

        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (!Auth.client) {
                setFeedback(MESSAGES.network);
                return;
            }
            const email = emailInput.value.trim();
            const password = passwordInput.value;
            const m = state.mode;

            if (m !== "reset" && !email) return setFeedback("Please enter your email address.");
            if ((m === "login" || m === "signup" || m === "reset") && !password) return setFeedback("Please enter your password.");
            if ((m === "signup" || m === "reset") && password.length < 6) return setFeedback("Password must be at least 6 characters.");
            if (m === "reset" && password !== confirmInput.value) return setFeedback("Passwords do not match.");

            submit.disabled = true;
            submit.classList.add("is-loading");
            setFeedback("");

            try {
                let result;
                if (m === "login") {
                    result = await Auth.client.auth.signInWithPassword({ email, password });
                } else if (m === "signup") {
                    result = await Auth.client.auth.signUp({ email, password, options: { data: { full_name: nameInput.value.trim() } } });
                } else if (m === "forgot") {
                    result = await Auth.client.auth.resetPasswordForEmail(email, { redirectTo: window.location.origin + "/?reset=1" });
                } else {
                    result = await Auth.client.auth.updateUser({ password });
                }
                if (result.error) throw result.error;

                if (m === "login") {
                    modal.close();
                    Toast.success("Signed in successfully.");
                } else if (m === "signup") {
                    if (result.data?.session) {
                        modal.close();
                        Toast.success("Account created. Welcome!");
                    } else {
                        setFeedback("Account created. Check your email to confirm your address, then log in.", "success");
                    }
                } else if (m === "forgot") {
                    setFeedback("If an account exists for this email, a reset link is on its way.", "success");
                } else {
                    modal.close();
                    Toast.success("Password updated.");
                }
            } catch (error) {
                setFeedback(error?.message || "Authentication failed. Please try again.");
            } finally {
                submit.disabled = false;
                submit.classList.remove("is-loading");
            }
        });

        const modal = Modal.open([
            el("div", { class: "modal-brand" }, [el("img", { src: "/frontend/assets/logo/logo.svg", width: "40", height: "40", alt: "" })]),
            title, subtitle, form,
        ], { labelledBy: "authTitle", onClose: () => { AuthModal.current = null; } });

        AuthModal.current = modal;
        render();
    },
};


/* =====================================================
   USER MENU (header)
   ===================================================== */

const UserMenu = {
    render() {
        const slot = document.getElementById("userSlot");
        if (!slot) return;
        slot.replaceChildren();

        if (!Auth.user) {
            slot.append(el("button", {
                class: "btn btn-primary btn-sm",
                type: "button",
                text: "Log in",
                onclick: () => AuthModal.open("login"),
            }));
            return;
        }

        const email = Auth.user.email || "Account";
        const initials = email.slice(0, 1).toUpperCase();
        const menu = el("div", { class: "menu", role: "menu", hidden: true }, [
            el("div", { class: "menu-header" }, [
                el("div", { class: "menu-email", text: email }),
                Auth.isAdmin ? el("span", { class: "pill pill-accent", text: "Admin" }) : null,
            ]),
            Auth.isAdmin ? el("a", { class: "menu-item", href: "/admin", role: "menuitem" }, [
                el("span", { html: ICONS.shield }), "Admin panel",
            ]) : null,
            el("button", { class: "menu-item danger", type: "button", role: "menuitem", onclick: () => Auth.logout() }, [
                el("span", { html: ICONS.logout }), "Log out",
            ]),
        ]);
        const trigger = el("button", {
            class: "avatar-btn",
            type: "button",
            "aria-haspopup": "menu",
            "aria-expanded": "false",
            title: email,
        }, [el("span", { class: "avatar-circle", text: initials })]);

        const toggle = (open) => {
            menu.hidden = !open;
            trigger.setAttribute("aria-expanded", String(open));
        };
        trigger.addEventListener("click", (event) => {
            event.stopPropagation();
            toggle(menu.hidden);
        });
        document.addEventListener("click", (event) => {
            if (!slot.contains(event.target)) toggle(false);
        });
        document.addEventListener("keydown", (event) => { if (event.key === "Escape") toggle(false); });

        slot.append(el("div", { class: "user-menu" }, [trigger, menu]));
    },
};


/* =====================================================
   BACKEND STATUS
   ===================================================== */

function initStatus() {
    const status = document.getElementById("status");
    const text = document.getElementById("statusText");
    if (!status) return;

    const check = async () => {
        if (document.hidden) return;
        try {
            const response = await fetch("/health", { cache: "no-store" });
            if (!response.ok) throw new Error();
            status.className = "status online";
            text.textContent = "Online";
        } catch (_) {
            status.className = "status offline";
            text.textContent = "Offline";
        }
    };
    check();
    setInterval(check, 30000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) check(); });
}


/* =====================================================
   BOOT
   ===================================================== */

document.addEventListener("DOMContentLoaded", () => {
    initBackground();
    Theme.init();
    initStatus();
    UserMenu.render();
    Auth.init();
});
