/* =====================================================
   Cortexa AI — profile page
   Data: GET/PATCH /profile/me (Supabase Auth + public.profiles)
   ===================================================== */

"use strict";

const content = () => document.getElementById("pageContent");

const PROVIDER_LABELS = { email: "Email and password", google: "Google" };

function profileGate() {
    content().replaceChildren(el("div", { class: "page-gate" }, [
        el("div", { class: "gate-card" }, [
            el("h2", { class: "modal-title", text: "Sign in to see your profile" }),
            el("p", { class: "modal-text", text: "Your profile and account details are available after you log in." }),
            el("button", { class: "btn btn-primary", type: "button", text: "Log in", onclick: () => AuthModal.open("login") }),
        ]),
    ]));
}

function profileSkeleton() {
    const line = (w, h = 12) => el("span", { class: "skeleton", style: `display:block;width:${w};height:${h}px;margin:8px 0` });
    content().replaceChildren(
        el("section", { class: "card-section" }, [el("div", { class: "profile-hero" }, [
            el("span", { class: "avatar-circle xl skeleton" }),
            el("div", { style: "flex:1" }, [line("40%", 20), line("55%")]),
        ])]),
        el("section", { class: "card-section" }, [el("div", { class: "card-body" }, [line("30%", 16), line("100%", 36)])]),
        el("section", { class: "card-section" }, [el("div", { class: "card-body" }, [line("25%", 16), line("80%"), line("70%"), line("60%")])]),
    );
}

function row(label, value, { mono = false, hint = "" } = {}) {
    return el("div", { class: "row" }, [
        el("div", { class: "row-label" }, [el("strong", { text: label }), hint ? el("span", { text: hint }) : null]),
        value instanceof Node ? value : el("div", { class: `row-value${mono ? " mono" : ""}`, text: value ?? "—" }),
    ]);
}

function renderProfile(p) {
    const avatarUser = { email: p.email, user_metadata: { full_name: p.full_name, avatar_url: p.avatar_url } };
    const displayName = p.full_name || p.email;

    // ---- header ----
    const hero = el("section", { class: "card-section" }, [el("div", { class: "profile-hero" }, [
        UserMenu.avatar(avatarUser, "xl"),
        el("div", {}, [
            el("div", { class: "profile-name", text: displayName }),
            p.full_name ? el("div", { class: "profile-email", text: p.email }) : null,
            el("div", { class: "profile-tags" }, [
                p.role === "ADMIN" ? el("span", { class: "pill pill-accent", text: "Admin" }) : el("span", { class: "pill", text: "Member" }),
                p.created_at ? el("span", { class: "pill", text: `Joined ${formatDate(p.created_at, false)}` }) : null,
            ]),
        ]),
    ])]);

    // ---- edit profile ----
    const nameInput = el("input", {
        class: "input", type: "text", maxlength: "80", value: p.full_name || "",
        placeholder: "Your full name", "aria-label": "Full name", autocomplete: "name",
        disabled: p.profile_editable ? null : true,
    });
    const hint = el("div", { class: "form-hint" });
    const save = el("button", { class: "btn btn-primary", type: "submit", text: "Save", disabled: true });
    const form = el("form", { class: "inline-form", novalidate: true }, [nameInput, save]);

    nameInput.addEventListener("input", () => {
        save.disabled = !nameInput.value.trim() || nameInput.value.trim() === (p.full_name || "");
        hint.textContent = "";
        hint.className = "form-hint";
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const value = nameInput.value.trim();
        if (!value || save.classList.contains("is-loading")) return;
        save.disabled = true;
        save.classList.add("is-loading");
        nameInput.disabled = true;
        try {
            const updated = await Api.request("/profile/me", { method: "PATCH", json: { full_name: value } });
            p.full_name = updated.full_name;
            hint.textContent = "Profile saved.";
            hint.className = "form-hint success";
            hero.querySelector(".profile-name").textContent = p.full_name || p.email;
            Toast.success("Profile updated.");
        } catch (error) {
            hint.textContent = error.status === 503
                ? "Editing your profile is temporarily unavailable."
                : "Unable to save your profile. Please try again.";
            hint.className = "form-hint error";
        } finally {
            save.classList.remove("is-loading");
            nameInput.disabled = false;
            save.disabled = nameInput.value.trim() === (p.full_name || "");
        }
    });

    const edit = el("section", { class: "card-section", style: "animation-delay:60ms" }, [
        el("header", {}, [el("h2", { text: "Edit profile" }), el("p", { text: "This name is shown on your profile." })]),
        el("div", { class: "card-body" }, [
            form,
            p.profile_editable ? hint : el("div", { class: "form-hint", text: "Editing your profile is temporarily unavailable." }),
        ]),
    ]);

    // ---- account ----
    const providers = (p.providers || [p.provider]).map((x) => PROVIDER_LABELS[x] || x).join(", ");
    const idValue = el("div", { class: "row-value mono" }, [
        p.id.slice(0, 8) + "…",
        " ",
        el("button", { class: "link-btn", type: "button", text: "Copy", onclick: async (e) => {
            try { await navigator.clipboard.writeText(p.id); e.target.textContent = "Copied"; } catch (_) { Toast.error("Copy failed."); }
        } }),
    ]);
    const account = el("section", { class: "card-section", style: "animation-delay:120ms" }, [
        el("header", {}, [el("h2", { text: "Account" })]),
        row("Email", p.email, { hint: p.email_confirmed ? "Verified" : "Not verified" }),
        row("Sign-in method", providers),
        row("Account created", formatDate(p.created_at)),
        row("Last sign-in", formatDate(p.last_sign_in_at)),
        row("User ID", idValue),
    ]);

    // ---- usage (only counts the backend returned) ----
    const usage = p.usage || {};
    const tiles = [
        ["Chats", usage.chats],
        ["Questions asked", usage.messages],
        ["PDFs uploaded", usage.pdfs],
        ["Images uploaded", usage.images],
    ].filter(([, value]) => typeof value === "number");

    const usageSection = tiles.length ? el("section", { class: "card-section", style: "animation-delay:180ms" }, [
        el("header", {}, [el("h2", { text: "Usage" }), el("p", { text: "Counted from your saved chats and uploads." })]),
        el("div", { class: "card-body" }, [el("div", { class: "stats-grid" }, tiles.map(([label, value], i) =>
            el("div", { class: "stat-tile", style: `animation-delay:${200 + i * 50}ms` }, [el("span", { text: label }), el("strong", { text: value.toLocaleString() })])
        ))]),
    ]) : null;

    content().replaceChildren(hero, edit, account, usageSection || "");
}

function profileError(message, onRetry) {
    content().replaceChildren(el("section", { class: "card-section" }, [el("div", { class: "card-body table-error" }, [
        el("span", { text: message }),
        el("button", { class: "btn btn-outline btn-sm", type: "button", text: "Try again", onclick: onRetry }),
    ])]));
}

async function loadProfile() {
    if (!Auth.resolved) return profileSkeleton();
    if (Auth.status === "error") return profileError(Auth.error || MESSAGES.authUnavailable, () => Auth.retry());
    if (!Auth.user) return profileGate();
    profileSkeleton();
    try {
        renderProfile(await Api.get("/profile/me"));
    } catch (error) {
        if (error.status === 401) return profileGate();   // session really expired
        profileError(error.status === 503 ? error.message : "We couldn't load your profile.", loadProfile);
    }
}

document.addEventListener("DOMContentLoaded", () => {
    profileSkeleton();
    Auth.onChange(loadProfile);
});
