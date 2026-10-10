/* =====================================================
   Cortexa AI — settings page
   Every control here is backed by a working feature:
   - Appearance / Voice: saved on this device (Prefs)
   - Password, sign-out everywhere: Supabase Auth
   - Clear chat history: POST /conversations/archive-all
   ===================================================== */

"use strict";

const content = () => document.getElementById("pageContent");

const VOICE_LANGUAGES = [
    ["", "Detect automatically"],
    ["en", "English"],
    ["hi", "Hindi"],
];

function section(id, title, description, children, delay = 0) {
    return el("section", { class: "card-section", id, style: `animation-delay:${delay}ms` }, [
        el("header", {}, [el("h2", { text: title }), description ? el("p", { text: description }) : null]),
        ...children,
    ]);
}

function settingRow(label, hint, control) {
    return el("div", { class: "row" }, [
        el("div", { class: "row-label" }, [el("strong", { text: label }), hint ? el("span", { text: hint }) : null]),
        control,
    ]);
}

function flashSaved(node) {
    node.classList.add("show");
    clearTimeout(node._timer);
    node._timer = setTimeout(() => node.classList.remove("show"), 1600);
}

/* ---------------- appearance (works signed in or out) ---------------- */

function appearanceSection() {
    const saved = el("span", { class: "saved-flag", text: "Saved" });
    const options = [["light", "Light"], ["dark", "Dark"], ["system", "System"]];
    const group = el("div", { class: "segmented", role: "group", "aria-label": "Theme" });

    const render = () => group.replaceChildren(...options.map(([value, label]) => el("button", {
        type: "button",
        "aria-pressed": String(Theme.preference() === value),
        text: label,
        onclick: () => {
            Theme.set(value);
            render();
            flashSaved(saved);
        },
    })));
    render();

    return section("appearance", "Appearance", "Saved on this device.", [
        settingRow("Theme", "System follows your device's light or dark mode.", el("div", {}, [group, saved])),
    ]);
}

/* ---------------- voice ---------------- */

function voiceSection() {
    const saved = el("span", { class: "saved-flag", text: "Saved" });

    const language = el("select", { class: "select", "aria-label": "Voice input language", style: "width:auto" },
        VOICE_LANGUAGES.map(([value, label]) => el("option", { value, text: label })));
    language.value = Prefs.get("voice.language", "");
    language.addEventListener("change", () => {
        Prefs.set("voice.language", language.value);
        flashSaved(saved);
    });

    const auto = el("input", { type: "checkbox", role: "switch", "aria-label": "Send automatically after transcription" });
    auto.checked = Prefs.get("voice.autoSend") === "true";
    auto.addEventListener("change", () => {
        Prefs.set("voice.autoSend", String(auto.checked));
        flashSaved(saved);
    });

    return section("voice", "Voice input", "Saved on this device.", [
        settingRow("Language", "Setting the spoken language can improve accuracy.", el("div", {}, [language, saved])),
        settingRow("Send automatically", "Send the message as soon as your speech is transcribed.",
            el("label", { class: "switch" }, [auto, el("span")])),
    ], 60);
}

/* ---------------- account & security (signed in) ---------------- */

function accountSection(profile) {
    const providers = profile.providers || [profile.provider];
    return section("account", "Account", null, [
        settingRow("Email", null, el("div", { class: "row-value", text: profile.email })),
        settingRow("Sign-in method", null, el("div", { class: "row-value", text: providers.map((p) => (p === "email" ? "Email and password" : p.charAt(0).toUpperCase() + p.slice(1))).join(", ") })),
        settingRow("Profile", "Name and account details.", el("a", { class: "btn btn-outline btn-sm", href: "/profile", text: "Open profile" })),
        profile.role === "ADMIN"
            ? settingRow("Administration", "Users, activity and system health.", el("a", { class: "btn btn-outline btn-sm", href: "/admin", text: "Admin panel" }))
            : null,
    ], 120);
}

function securitySection(profile) {
    const children = [];
    const providers = profile.providers || [profile.provider];

    if (providers.includes("email")) {
        const pass = el("input", { class: "input", type: "password", placeholder: "New password", autocomplete: "new-password", "aria-label": "New password" });
        const confirm = el("input", { class: "input", type: "password", placeholder: "Confirm new password", autocomplete: "new-password", "aria-label": "Confirm new password" });
        const hint = el("div", { class: "form-hint" });
        const submit = el("button", { class: "btn btn-primary", type: "submit", text: "Update password" });
        const form = el("form", { class: "stack", novalidate: true }, [pass, confirm, el("div", {}, [submit]), hint]);

        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (submit.classList.contains("is-loading")) return;
            const setHint = (text, type = "error") => { hint.textContent = text; hint.className = `form-hint ${type}`; };
            if (pass.value.length < 6) return setHint("Password must be at least 6 characters.");
            if (pass.value !== confirm.value) return setHint("Passwords do not match.");

            submit.classList.add("is-loading");
            submit.disabled = true;
            try {
                const { error } = await Auth.client.auth.updateUser({ password: pass.value });
                if (error) throw error;
                form.reset();
                setHint("Password updated.", "success");
                Toast.success("Password updated.");
            } catch (error) {
                setHint(error?.message || "Unable to update your password. Please try again.");
            } finally {
                submit.classList.remove("is-loading");
                submit.disabled = false;
            }
        });

        children.push(el("div", { class: "row", style: "display:block" }, [
            el("div", { class: "row-label", style: "margin-bottom:10px" }, [el("strong", { text: "Change password" })]),
            form,
        ]));
    }

    const everywhere = el("button", { class: "btn btn-outline btn-sm", type: "button", text: "Log out everywhere" });
    everywhere.addEventListener("click", async () => {
        const ok = await Modal.confirm({
            title: "Log out of all devices?",
            message: "You will be signed out on every browser and device, including this one.",
            confirmText: "Log out everywhere",
        });
        if (!ok) return;
        everywhere.classList.add("is-loading");
        everywhere.disabled = true;
        try {
            const { error } = await Auth.client.auth.signOut({ scope: "global" });
            if (error) throw error;
            window.location.assign("/");
        } catch (_) {
            Toast.error("Unable to log out of all devices. Please try again.");
            everywhere.classList.remove("is-loading");
            everywhere.disabled = false;
        }
    });
    children.push(settingRow("Active sessions", "Sign out on every device where you are logged in.", everywhere));

    return section("security", "Security", null, children, 180);
}

function dataSection() {
    const clear = el("button", { class: "btn btn-danger-outline btn-sm", type: "button", text: "Delete all chats" });
    clear.addEventListener("click", async () => {
        const ok = await Modal.confirm({
            title: "Delete all chats?",
            message: "Every conversation will be removed from your chat history. This cannot be undone from the app.",
            confirmText: "Delete all chats",
            danger: true,
        });
        if (!ok) return;
        clear.classList.add("is-loading");
        clear.disabled = true;
        try {
            const result = await Api.post("/conversations/archive-all");
            Toast.success(result.archived ? `${result.archived} chat${result.archived === 1 ? "" : "s"} deleted.` : "There were no chats to delete.");
        } catch (_) {
            Toast.error("Unable to delete your chats. Please try again.");
        } finally {
            clear.classList.remove("is-loading");
            clear.disabled = false;
        }
    });

    return section("data", "Data", null, [
        settingRow("Chat history", "Remove all conversations from your chat list.", clear),
    ], 240);
}

/* ---------------- page ---------------- */

function layout(sections) {
    const links = sections.filter(Boolean).map((s) => [s.id, s.querySelector("h2").textContent]);
    const nav = el("nav", { class: "settings-nav", "aria-label": "Settings sections" },
        links.map(([id, label], i) => el("a", { href: `#${id}`, class: i === 0 ? "active" : "", text: label })));

    nav.addEventListener("click", (event) => {
        const link = event.target.closest("a");
        if (!link) return;
        nav.querySelectorAll("a").forEach((a) => a.classList.toggle("active", a === link));
    });

    content().replaceChildren(el("div", { class: "settings-layout" }, [nav, el("div", {}, sections.filter(Boolean))]));
}

function accountPlaceholder() {
    return section("account", "Account", null, [el("div", { class: "card-body" }, [
        el("span", { class: "skeleton skeleton-line", style: "width:60%" }),
        el("span", { class: "skeleton skeleton-line", style: "width:40%" }),
    ])], 120);
}

async function loadSettings() {
    const local = [appearanceSection(), voiceSection()];

    if (!Auth.resolved) {
        layout([...local, accountPlaceholder()]);
        return;
    }
    if (Auth.status === "error") {
        layout([...local, section("account", "Account", null, [
            el("div", { class: "card-body table-error" }, [
                el("span", { text: Auth.error || MESSAGES.authUnavailable }),
                el("button", { class: "btn btn-outline btn-sm", type: "button", text: "Try again", onclick: () => Auth.retry() }),
            ]),
        ], 120)]);
        return;
    }
    if (!Auth.user) {
        layout([...local, section("account", "Account", null, [
            el("div", { class: "card-body" }, [
                el("p", { class: "modal-text", text: "Log in to manage your account, security and chat history." }),
                el("button", { class: "btn btn-primary btn-sm", type: "button", text: "Log in", onclick: () => AuthModal.open("login") }),
            ]),
        ], 120)]);
        return;
    }

    const loading = accountPlaceholder();
    layout([...local, loading]);

    try {
        const profile = await Api.get("/profile/me");
        layout([...local, accountSection(profile), securitySection(profile), dataSection()]);
    } catch (error) {
        if (error.status === 401) return;   // session really expired: the login dialog is already open
        loading.querySelector(".card-body").replaceChildren(el("div", { class: "table-error" }, [
            el("span", { text: error.status === 503 ? error.message : "We couldn't load your account settings." }),
            el("button", { class: "btn btn-outline btn-sm", type: "button", text: "Try again", onclick: loadSettings }),
        ]));
    }
}

document.addEventListener("DOMContentLoaded", () => {
    loadSettings();
    Auth.onChange(loadSettings);
});
