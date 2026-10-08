/* =====================================================
   Agentic AI - shared frontend logic

   - Animated background (all pages)
   - Theme toggle + backend status (all pages)
   - Chat + router animation (index.html only)
   - Streaming AI response
   - Single /chat/stream request
   ===================================================== */


/* =====================================================
   API
   ===================================================== */

const STREAM_CHAT_URL = "/chat/stream";
const STATUS_URL = "/openapi.json";
const VOICE_TRANSCRIBE_URL = "/voice/transcribe";

/* ================= FILE UPLOAD ================= */
const PDF_UPLOAD_URL = "/upload/pdf";
const IMAGE_UPLOAD_URL = "/upload/image";
let selectedDocumentId = null;
let selectedImagePath = null;
let selectedFileName = "";
let selectedFileType = "";
let selectedFileUploading = false;


const CONVERSATIONS_URL = "/conversations";
const CONVERSATION_MESSAGES_URL = (id) =>
    `/conversations/${encodeURIComponent(id)}/messages`;


/* =====================================================
   ROUTES
   ===================================================== */

const ROUTES = [
    "DIRECT",
    "WIKIPEDIA",
    "TAVILY",
    "ARXIV"
];


const ROUTE_STATUS = {
    DIRECT: "Generating response...",
    WIKIPEDIA: "Reading Wikipedia information...",
    TAVILY: "Searching the web...",
    ARXIV: "Searching research papers..."
};


/* =====================================================
   HELPERS
   ===================================================== */
function escapeHTML(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

window.escapeHTML = escapeHTML;
const sleep = (ms) =>
    new Promise((r) => setTimeout(r, ms));


/* =====================================================
   THEME
   ===================================================== */

function applyTheme(theme) {

    document.documentElement.setAttribute(
        "data-theme",
        theme
    );

    localStorage.setItem(
        "theme",
        theme
    );
}


applyTheme(
    localStorage.getItem("theme") || "light"
);


/* =====================================================
   ANIMATED BACKGROUND
   ===================================================== */

function initBackground() {

    const bg = document.createElement("div");

    bg.className = "bg";

    bg.setAttribute(
        "aria-hidden",
        "true"
    );

    bg.innerHTML =
        '<span class="orb o1"></span>' +
        '<span class="orb o2"></span>' +
        '<span class="orb o3"></span>' +
        '<span class="grid"></span>' +
        '<canvas id="bgCanvas"></canvas>';

    document.body.prepend(bg);


    if (
        window.matchMedia(
            "(prefers-reduced-motion: reduce)"
        ).matches
    ) {
        return;
    }


    const canvas =
        bg.querySelector("canvas");

    const ctx =
        canvas.getContext("2d");


    let w = 0;
    let h = 0;
    let points = [];

    let color =
        "99,102,241";

    let tick = 0;


    function resize() {

        w = canvas.width =
            window.innerWidth;

        h = canvas.height =
            window.innerHeight;


        const count = Math.min(
            70,
            Math.floor(
                (w * h) / 20000
            )
        );


        points = Array.from(
            { length: count },
            () => ({
                x: Math.random() * w,
                y: Math.random() * h,
                vx:
                    (Math.random() - 0.5)
                    * 0.35,
                vy:
                    (Math.random() - 0.5)
                    * 0.35
            })
        );
    }


    function frame() {

        if (!document.hidden) {

            if (tick++ % 30 === 0) {

                color =
                    getComputedStyle(
                        document.documentElement
                    )
                        .getPropertyValue(
                            "--particle"
                        )
                        .trim() || color;
            }


            ctx.clearRect(
                0,
                0,
                w,
                h
            );


            for (
                let i = 0;
                i < points.length;
                i++
            ) {

                const p = points[i];


                p.x += p.vx;
                p.y += p.vy;


                if (
                    p.x < 0 ||
                    p.x > w
                ) {
                    p.vx *= -1;
                }


                if (
                    p.y < 0 ||
                    p.y > h
                ) {
                    p.vy *= -1;
                }


                ctx.beginPath();

                ctx.arc(
                    p.x,
                    p.y,
                    1.6,
                    0,
                    Math.PI * 2
                );


                ctx.fillStyle =
                    `rgba(${color},0.55)`;

                ctx.fill();


                for (
                    let j = i + 1;
                    j < points.length;
                    j++
                ) {

                    const q = points[j];


                    const d = Math.hypot(
                        p.x - q.x,
                        p.y - q.y
                    );


                    if (d < 130) {

                        ctx.beginPath();

                        ctx.moveTo(
                            p.x,
                            p.y
                        );

                        ctx.lineTo(
                            q.x,
                            q.y
                        );


                        ctx.strokeStyle =
                            `rgba(${color},${0.16 * (1 - d / 130)})`;

                        ctx.lineWidth = 1;

                        ctx.stroke();
                    }
                }
            }
        }


        requestAnimationFrame(frame);
    }


    window.addEventListener(
        "resize",
        resize
    );

    resize();

    frame();
}



/* =====================================================
   SUPABASE AUTH
   ===================================================== */

const SUPABASE_URL =
    "https://fwdxzyyodatplmvyjozt.supabase.co";

/*
   IMPORTANT:
   Replace the value below with your Supabase
   Publishable/Anon key.

   NEVER put sb_secret_... or a service-role key here.
*/
const SUPABASE_PUBLISHABLE_KEY =
    "sb_publishable_wlq0SUlBijqChhosoxj64w_Q27LbZvu";

let supabaseClient = null;
let currentSession = null;
let currentUser = null;

let currentConversationId = null;
let conversations = [];

// ============================================================
// GUEST SESSION
// ============================================================

const GUEST_SESSION_KEY = "agentic_ai_guest_session_id";
const GUEST_PROMPT_SHOWN_KEY = "agentic_ai_guest_prompt_shown";

function getGuestSessionId() {
    let guestId = localStorage.getItem(GUEST_SESSION_KEY);

    if (!guestId) {
        if (window.crypto?.randomUUID) {
            guestId = window.crypto.randomUUID();
        } else {
            guestId = "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
                const r = Math.random() * 16 | 0;
                const v = c === "x" ? r : (r & 0x3 | 0x8);
                return v.toString(16);
            });
        }

        localStorage.setItem(GUEST_SESSION_KEY, guestId);
    }

    return guestId;
}

function getGuestHeaders() {
    return {
        "X-Guest-Session-ID": getGuestSessionId()
    };
}

async function getRequestHeaders(extraHeaders = {}) {
    const authHeaders = await getAuthHeaders();

    return {
        ...extraHeaders,
        ...(Object.keys(authHeaders).length > 0
            ? authHeaders
            : getGuestHeaders())
    };
}

let mediaRecorder = null;
let voiceStream = null;
let voiceChunks = [];
let isRecordingVoice = false;

// File selected by the user but waiting for authentication.
// The browser keeps the File object in memory; nothing is uploaded
// or inserted into Supabase until the user is authenticated.
let pendingFileAfterLogin = null;
let pendingFileNeedsLogin = false;

function loadSupabaseScript() {
    return new Promise((resolve, reject) => {
        if (window.supabase) {
            resolve();
            return;
        }

        const existing = document.querySelector(
            'script[data-supabase-js="true"]'
        );

        if (existing) {
            existing.addEventListener("load", resolve, { once: true });
            existing.addEventListener("error", reject, { once: true });
            return;
        }

        const script = document.createElement("script");
        script.src =
            "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2";
        script.dataset.supabaseJs = "true";
        script.onload = resolve;
        script.onerror = () =>
            reject(new Error("Unable to load Supabase JS."));
        document.head.appendChild(script);
    });
}

function authMessage(message, isError = false) {
    const el = document.getElementById("authMessage");
    if (!el) return;

    el.textContent = message || "";
    el.style.color = isError ? "#dc2626" : "#16a34a";
}

let passwordRecoveryMode = false;

function createAuthUI() {
    if (document.getElementById("authContainer")) {
        return;
    }

    const container = document.createElement("div");
    container.id = "authContainer";

    Object.assign(container.style, {
        position: "fixed",
        top: "18px",
        right: "18px",
        zIndex: "9999",
        fontFamily: "inherit"
    });

    container.innerHTML = `
        <button id="authLoginButton" type="button"
            style="
                border:1px solid rgba(99,102,241,.35);
                background:rgba(255,255,255,.92);
                color:#111827;
                padding:9px 16px;
                border-radius:10px;
                cursor:pointer;
                font-weight:600;
                box-shadow:0 5px 20px rgba(0,0,0,.08);
            ">
            Login
        </button>

        <div id="authUserMenu" style="display:none;"></div>

        <div id="authModal" style="
            display:none;
            position:fixed;
            inset:0;
            background:rgba(0,0,0,.55);
            align-items:center;
            justify-content:center;
            padding:20px;
        ">
            <div style="
                width:min(420px,100%);
                background:var(--card,#ffffff);
                color:var(--text,#111827);
                border-radius:16px;
                padding:24px;
                box-shadow:0 20px 60px rgba(0,0,0,.25);
            ">
                <div style="
                    display:flex;
                    justify-content:space-between;
                    align-items:center;
                    margin-bottom:18px;
                ">
                    <h2 id="authTitle" style="margin:0;">Login</h2>

                    <button id="authCloseButton" type="button"
                        style="
                            border:0;
                            background:transparent;
                            font-size:22px;
                            cursor:pointer;
                        ">
                        ×
                    </button>
                </div>

                <div id="authNameField" style="display:none;margin-bottom:12px;">
                    <input id="authFullName" type="text"
                        placeholder="Full name"
                        autocomplete="name"
                        style="
                            width:100%;
                            box-sizing:border-box;
                            padding:11px;
                            border:1px solid #d1d5db;
                            border-radius:9px;
                        ">
                </div>

                <input id="authEmail" type="email"
                    placeholder="Email"
                    autocomplete="email"
                    style="
                        width:100%;
                        box-sizing:border-box;
                        padding:11px;
                        border:1px solid #d1d5db;
                        border-radius:9px;
                        margin-bottom:12px;
                    ">

                <div id="authPasswordField">
                    <input id="authPassword" type="password"
                        placeholder="Password"
                        autocomplete="current-password"
                        style="
                            width:100%;
                            box-sizing:border-box;
                            padding:11px;
                            border:1px solid #d1d5db;
                            border-radius:9px;
                            margin-bottom:8px;
                        ">
                </div>

                <div id="authConfirmPasswordField" style="display:none;">
                    <input id="authConfirmPassword" type="password"
                        placeholder="Confirm new password"
                        autocomplete="new-password"
                        style="
                            width:100%;
                            box-sizing:border-box;
                            padding:11px;
                            border:1px solid #d1d5db;
                            border-radius:9px;
                            margin-bottom:12px;
                        ">
                </div>

                <button id="forgotPasswordButton" type="button"
                    style="
                        display:block;
                        border:0;
                        background:transparent;
                        color:#6366f1;
                        cursor:pointer;
                        padding:0;
                        margin:0 0 14px auto;
                        font-size:13px;
                    ">
                    Forgot password?
                </button>

                <button id="authSubmitButton" type="button"
                    style="
                        width:100%;
                        padding:11px;
                        border:0;
                        border-radius:9px;
                        background:#6366f1;
                        color:white;
                        cursor:pointer;
                        font-weight:600;
                    ">
                    Login
                </button>

                <button id="googleAuthButton" type="button"
                    style="
                        width:100%;
                        padding:11px;
                        margin-top:10px;
                        border:1px solid #d1d5db;
                        border-radius:9px;
                        background:transparent;
                        color:inherit;
                        cursor:pointer;
                        font-weight:600;
                    ">
                    Continue with Google
                </button>

                <div id="authMessage"
                    style="
                        min-height:20px;
                        margin-top:12px;
                        font-size:13px;
                    ">
                </div>

                <button id="authModeButton" type="button"
                    style="
                        border:0;
                        background:transparent;
                        color:#6366f1;
                        cursor:pointer;
                        padding:0;
                        font-size:13px;
                    ">
                    Create an account
                </button>
            </div>
        </div>
    `;

    document.body.appendChild(container);

    const modal = document.getElementById("authModal");
    const loginButton = document.getElementById("authLoginButton");
    const closeButton = document.getElementById("authCloseButton");
    const submitButton = document.getElementById("authSubmitButton");
    const googleButton = document.getElementById("googleAuthButton");
    const modeButton = document.getElementById("authModeButton");
    const forgotButton = document.getElementById("forgotPasswordButton");

    let signupMode = false;
    let forgotMode = false;
    let resetMode = false;

    const openModal = () => {
        modal.style.display = "flex";
        authMessage("");
    };

    const closeModal = () => {
        modal.style.display = "none";
        authMessage("");
        resetAuthFields();
    };

    const resetAuthFields = () => {
        const email = document.getElementById("authEmail");
        const password = document.getElementById("authPassword");
        const confirmPassword = document.getElementById("authConfirmPassword");
        const fullName = document.getElementById("authFullName");

        if (email) email.value = "";
        if (password) password.value = "";
        if (confirmPassword) confirmPassword.value = "";
        if (fullName) fullName.value = "";
    };

    const updateMode = () => {
        const title = document.getElementById("authTitle");
        const nameField = document.getElementById("authNameField");
        const passwordField = document.getElementById("authPasswordField");
        const confirmPasswordField = document.getElementById("authConfirmPasswordField");

        if (resetMode) {
            title.textContent = "Reset password";
            nameField.style.display = "none";
            passwordField.style.display = "block";
            confirmPasswordField.style.display = "block";
            document.getElementById("authPassword").placeholder = "New password";
            document.getElementById("authPassword").autocomplete = "new-password";
            forgotButton.style.display = "none";
            submitButton.textContent = "Update password";
            googleButton.style.display = "none";
            modeButton.style.display = "none";
            return;
        }

        if (forgotMode) {
            title.textContent = "Forgot password";
            nameField.style.display = "none";
            passwordField.style.display = "none";
            confirmPasswordField.style.display = "none";
            forgotButton.style.display = "none";
            submitButton.textContent = "Send reset email";
            googleButton.style.display = "none";
            modeButton.style.display = "block";
            modeButton.textContent = "Back to login";
            return;
        }

        title.textContent = signupMode ? "Create account" : "Login";
        nameField.style.display = signupMode ? "block" : "none";
        passwordField.style.display = "block";
        confirmPasswordField.style.display = "none";
        document.getElementById("authPassword").placeholder = "Password";
        document.getElementById("authPassword").autocomplete = "current-password";
        forgotButton.style.display = signupMode ? "none" : "block";
        submitButton.textContent = signupMode ? "Sign up" : "Login";
        googleButton.style.display = "block";
        modeButton.style.display = "block";
        modeButton.textContent = signupMode
            ? "Already have an account? Login"
            : "Create an account";
    };

    loginButton.addEventListener("click", () => {
        signupMode = false;
        forgotMode = false;
        resetMode = false;
        updateMode();
        openModal();
    });

    closeButton.addEventListener("click", closeModal);

    modal.addEventListener("click", (event) => {
        if (event.target === modal) {
            closeModal();
        }
    });

    modeButton.addEventListener("click", () => {
        if (forgotMode) {
            signupMode = false;
            forgotMode = false;
            resetMode = false;
        } else {
            signupMode = !signupMode;
            forgotMode = false;
            resetMode = false;
        }

        resetAuthFields();
        updateMode();
        authMessage("");
    });

    forgotButton.addEventListener("click", () => {
        signupMode = false;
        forgotMode = true;
        resetMode = false;
        resetAuthFields();
        updateMode();
        authMessage("");
        document.getElementById("authEmail").focus();
    });

    submitButton.addEventListener("click", async () => {
        const email = document.getElementById("authEmail").value.trim();
        const password = document.getElementById("authPassword").value;
        const confirmPassword = document.getElementById("authConfirmPassword").value;
        const fullName = document.getElementById("authFullName").value.trim();

        if (resetMode || passwordRecoveryMode) {
            if (!password || !confirmPassword) {
                authMessage("Enter the new password twice.", true);
                return;
            }

            if (password.length < 6) {
                authMessage("Password must be at least 6 characters.", true);
                return;
            }

            if (password !== confirmPassword) {
                authMessage("Passwords do not match.", true);
                return;
            }

            submitButton.disabled = true;
            authMessage("Updating password...");

            try {
                const result = await updatePassword(password);

                if (result.error) {
                    throw result.error;
                }

                passwordRecoveryMode = false;

                authMessage("Password updated successfully. You can now login.");

                setTimeout(() => {
                    resetMode = false;
                    signupMode = false;
                    forgotMode = false;
                    updateMode();
                    resetAuthFields();
                }, 1200);
            } catch (error) {
                authMessage(
                    error?.message || "Unable to update password.",
                    true
                );
            } finally {
                submitButton.disabled = false;
            }

            return;
        }

        if (forgotMode) {
            if (!email) {
                authMessage("Enter your email address.", true);
                return;
            }

            submitButton.disabled = true;
            authMessage("Sending reset email...");

            try {
                const result = await sendPasswordResetEmail(email);

                if (result.error) {
                    throw result.error;
                }

                authMessage(
                    "Password reset email sent. Check your inbox and open the reset link."
                );
            } catch (error) {
                authMessage(
                    error?.message || "Unable to send reset email.",
                    true
                );
            } finally {
                submitButton.disabled = false;
            }

            return;
        }

        if (!email || !password) {
            authMessage("Email and password are required.", true);
            return;
        }

        submitButton.disabled = true;
        authMessage("Please wait...");

        try {
            if (signupMode) {
                const result = await signUpWithEmail(
                    email,
                    password,
                    fullName
                );

                if (result.error) {
                    throw result.error;
                }

                if (result.data?.session) {
                    closeModal();
                    await refreshAuthUI();
                } else {
                    authMessage(
                        "Account created. Check your email to confirm your account."
                    );
                }
            } else {
                const result = await loginWithEmail(
                    email,
                    password
                );

                if (result.error) {
                    throw result.error;
                }

                closeModal();
                await refreshAuthUI();
            }
        } catch (error) {
            authMessage(
                error?.message || "Authentication failed.",
                true
            );
        } finally {
            submitButton.disabled = false;
        }
    });

    googleButton.addEventListener("click", async () => {
        googleButton.disabled = true;
        authMessage("Opening Google login...");

        try {
            const result = await loginWithGoogle();

            if (result?.error) {
                throw result.error;
            }
        } catch (error) {
            authMessage(
                error?.message || "Google login failed.",
                true
            );
            googleButton.disabled = false;
        }
    });

    updateMode();
}

function hideGuestAuthPrompt() {
    const prompt = document.getElementById("guestAuthPrompt");
    if (prompt) {
        prompt.style.display = "none";
    }
}

function showGuestAuthPrompt() {
    if (!document.getElementById("chatMessages")) return;
    if (currentUser) return;
    if (sessionStorage.getItem(GUEST_PROMPT_SHOWN_KEY) === "true") return;

    let prompt = document.getElementById("guestAuthPrompt");

    if (!prompt) {
        prompt = document.createElement("div");
        prompt.id = "guestAuthPrompt";
        prompt.style.cssText = `
            position:fixed;
            inset:0;
            z-index:10001;
            display:flex;
            align-items:center;
            justify-content:center;
            padding:20px;
            background:rgba(0,0,0,.45);
        `;

        prompt.innerHTML = `
            <div style="
                width:min(420px,100%);
                background:var(--card,#ffffff);
                color:var(--text,#111827);
                border-radius:18px;
                padding:26px;
                box-shadow:0 24px 80px rgba(0,0,0,.28);
                text-align:center;
            ">
                <div style="font-size:34px;margin-bottom:8px;">🤖</div>
                <h2 style="margin:0 0 8px;">Save your conversations</h2>
                <p style="margin:0 0 20px;opacity:.75;line-height:1.5;">
                    You can continue as a guest, or create an account to keep your chats with your account.
                </p>
                <div style="display:flex;gap:10px;flex-wrap:wrap;justify-content:center;">
                    <button id="guestPromptLogin" type="button" style="
                        flex:1;min-width:110px;padding:11px 14px;border:0;border-radius:10px;
                        background:#6366f1;color:#fff;cursor:pointer;font-weight:600;
                    ">Login</button>
                    <button id="guestPromptSignup" type="button" style="
                        flex:1;min-width:110px;padding:11px 14px;border:1px solid #6366f1;border-radius:10px;
                        background:transparent;color:#6366f1;cursor:pointer;font-weight:600;
                    ">Sign up</button>
                    <button id="guestPromptContinue" type="button" style="
                        width:100%;padding:10px;border:0;background:transparent;color:inherit;
                        opacity:.7;cursor:pointer;
                    ">Continue as Guest</button>
                </div>
            </div>
        `;

        document.body.appendChild(prompt);

        document.getElementById("guestPromptLogin")?.addEventListener("click", () => {
            sessionStorage.setItem(GUEST_PROMPT_SHOWN_KEY, "true");
            hideGuestAuthPrompt();
            document.getElementById("authLoginButton")?.click();
        });

        document.getElementById("guestPromptSignup")?.addEventListener("click", () => {
            sessionStorage.setItem(GUEST_PROMPT_SHOWN_KEY, "true");
            hideGuestAuthPrompt();
            document.getElementById("authLoginButton")?.click();
            setTimeout(() => {
                document.getElementById("authModeButton")?.click();
            }, 50);
        });

        document.getElementById("guestPromptContinue")?.addEventListener("click", () => {
            sessionStorage.setItem(GUEST_PROMPT_SHOWN_KEY, "true");
            hideGuestAuthPrompt();
        });
    }

    prompt.style.display = "flex";
}

async function initSupabaseAuth() {
    try {
        await loadSupabaseScript();

        if (
            SUPABASE_PUBLISHABLE_KEY ===
            "YOUR_SUPABASE_PUBLISHABLE_KEY"
        ) {
            console.warn(
                "Supabase Auth: Add your Publishable/Anon key to app.js."
            );
            return;
        }

        supabaseClient =
            window.supabase.createClient(
                SUPABASE_URL,
                SUPABASE_PUBLISHABLE_KEY
            );

        /*
         * Listen for auth events first.
         * PASSWORD_RECOVERY can happen immediately after Supabase
         * processes the recovery link.
         */
        supabaseClient.auth.onAuthStateChange(
            async (event, sessionData) => {
                await handleAuthSession(sessionData);

                if (event === "PASSWORD_RECOVERY") {
                    passwordRecoveryMode = true;
                    openPasswordResetUI();
                }
            }
        );

        const {
            data: { session }
        } = await supabaseClient.auth.getSession();

        await handleAuthSession(session);

        /*
         * Fallback for password-reset links:
         * resetPasswordForEmail() redirects to /?reset=1.
         * The URL flag guarantees that the reset form opens even if
         * PASSWORD_RECOVERY fired before the listener was ready.
         */
        const params = new URLSearchParams(window.location.search);

        if (params.get("reset") === "1") {
            passwordRecoveryMode = true;

            setTimeout(() => {
                openPasswordResetUI();

                /*
                 * Remove ?reset=1 after the form opens so refreshing
                 * the page does not repeatedly open the reset form.
                 */
                const cleanUrl =
                    window.location.origin +
                    window.location.pathname;

                window.history.replaceState(
                    {},
                    document.title,
                    cleanUrl
                );
            }, 100);
        }
    } catch (error) {
        console.error(
            "Supabase Auth initialization failed:",
            error
        );
    }
}

async function migrateGuestSession() {
    if (!currentUser || !currentSession?.access_token) {
        return null;
    }

    const guestId = localStorage.getItem(GUEST_SESSION_KEY);

    if (!guestId) {
        return null;
    }

    try {
        const response = await fetch(
            "/auth/migrate-guest",
            {
                method: "POST",
                headers: {
                    Authorization:
                        `Bearer ${currentSession.access_token}`,
                    "X-Guest-Session-ID": guestId
                }
            }
        );

        let data = null;

        try {
            data = await response.json();
        } catch (_) {
            data = null;
        }

        if (!response.ok) {
            throw new Error(
                data?.detail ||
                `Guest history migration failed: HTTP ${response.status}`
            );
        }

        console.log(
            "Guest migration:",
            data
        );

        // Once the guest history is successfully claimed, this browser no
        // longer needs the old anonymous identity. A future logout will get
        // a fresh guest session instead of reusing the migrated one.
        localStorage.removeItem(GUEST_SESSION_KEY);

        return data;

    } catch (error) {
        // Keep the guest ID if migration failed. This allows a retry on the
        // next auth event/page refresh instead of losing guest history.
        console.warn(
            "Guest history migration failed:",
            error
        );
        return null;
    }
}


async function handleAuthSession(session) {
    const wasLoggedOut = !currentUser;

    currentSession = session || null;
    currentUser = session?.user || null;

    if (currentUser) {
        hideGuestAuthPrompt();

        // Phase 7.6: claim the guest history created by this browser before
        // loading the authenticated conversation list.
        await migrateGuestSession();
    }

    await refreshAuthUI();

    // If a PDF was selected before login, upload it automatically after
    // authentication succeeds. No temporary database row is needed.
    if (currentUser && wasLoggedOut && pendingFileAfterLogin) {
        const pendingFile = pendingFileAfterLogin;
        pendingFileAfterLogin = null;
        pendingFileNeedsLogin = false;

        await uploadSelectedFile(pendingFile);
    }

    setTimeout(() => {
        refreshConversationList();
    }, 0);

    if (currentSession) {
        try {
            await fetch("/auth/me", {
                method: "GET",
                headers: {
                    Authorization:
                        `Bearer ${currentSession.access_token}`
                }
            });
        } catch (error) {
            console.warn(
                "Backend auth verification failed:",
                error
            );
        }
    }
}

async function refreshAuthUI() {
    const loginButton =
        document.getElementById("authLoginButton");

    const userMenu =
        document.getElementById("authUserMenu");

    if (!loginButton || !userMenu) {
        return;
    }

    const attachButton = document.getElementById("attachFileButton");

    if (!currentUser) {
        if (attachButton) {
            attachButton.title = "Login required before uploading a file";
            attachButton.setAttribute("aria-label", "Login required before uploading a file");
        }
        loginButton.style.display = "block";
        userMenu.style.display = "none";
        return;
    }

    if (attachButton) {
        attachButton.title = "Upload PDF or image";
        attachButton.setAttribute("aria-label", "Upload PDF or image");
    }

    loginButton.style.display = "none";
    userMenu.style.display = "block";

    userMenu.innerHTML = `
        <div style="
            display:flex;
            align-items:center;
            gap:8px;
        ">
            <button id="authUserButton" type="button"
                style="
                    border:1px solid rgba(99,102,241,.35);
                    background:rgba(255,255,255,.92);
                    color:#111827;
                    padding:9px 12px;
                    border-radius:10px;
                    cursor:pointer;
                    font-weight:600;
                    box-shadow:0 5px 20px rgba(0,0,0,.08);
                ">
                ${escapeHTML(
                    currentUser.email || "User"
                )}
            </button>

            <button id="authLogoutButton" type="button"
                style="
                    border:1px solid #ef4444;
                    background:transparent;
                    color:#ef4444;
                    padding:9px 12px;
                    border-radius:10px;
                    cursor:pointer;
                    font-weight:600;
                ">
                Logout
            </button>
        </div>

        <div style="
            margin-top:7px;
            font-size:11px;
            opacity:.75;
            text-align:right;
        ">
            ID: ${escapeHTML(currentUser.id || "")}
        </div>
    `;

    document
        .getElementById("authLogoutButton")
        .addEventListener(
            "click",
            logoutUser
        );
}

async function signUpWithEmail(
    email,
    password,
    fullName = ""
) {
    if (!supabaseClient) {
        throw new Error(
            "Supabase Auth is not initialized."
        );
    }

    return await supabaseClient.auth.signUp({
        email,
        password,
        options: {
            data: {
                full_name: fullName
            }
        }
    });
}

async function loginWithEmail(email, password) {
    if (!supabaseClient) {
        throw new Error(
            "Supabase Auth is not initialized."
        );
    }

    return await supabaseClient.auth.signInWithPassword({
        email,
        password
    });
}

async function loginWithGoogle() {
    if (!supabaseClient) {
        throw new Error(
            "Supabase Auth is not initialized."
        );
    }

    return await supabaseClient.auth.signInWithOAuth({
        provider: "google",
        options: {
            redirectTo:
                window.location.origin + "/"
        }
    });
}

async function sendPasswordResetEmail(email) {
    if (!supabaseClient) {
        throw new Error("Supabase Auth is not initialized.");
    }

    const redirectTo = window.location.origin + "/?reset=1";

    return await supabaseClient.auth.resetPasswordForEmail(
        email,
        {
            redirectTo
        }
    );
}

async function updatePassword(newPassword) {
    if (!supabaseClient) {
        throw new Error("Supabase Auth is not initialized.");
    }

    return await supabaseClient.auth.updateUser({
        password: newPassword
    });
}

function openPasswordResetUI() {
    passwordRecoveryMode = true;

    const modal = document.getElementById("authModal");
    const title = document.getElementById("authTitle");
    const nameField = document.getElementById("authNameField");
    const passwordField = document.getElementById("authPasswordField");
    const confirmPasswordField = document.getElementById("authConfirmPasswordField");
    const forgotButton = document.getElementById("forgotPasswordButton");
    const submitButton = document.getElementById("authSubmitButton");
    const googleButton = document.getElementById("googleAuthButton");
    const modeButton = document.getElementById("authModeButton");
    const emailField = document.getElementById("authEmail");

    if (!modal || !submitButton) {
        return;
    }

    title.textContent = "Reset password";
    nameField.style.display = "none";
    emailField.style.display = "none";
    passwordField.style.display = "block";
    confirmPasswordField.style.display = "block";
    forgotButton.style.display = "none";
    submitButton.textContent = "Update password";
    googleButton.style.display = "none";
    modeButton.style.display = "none";

    const password = document.getElementById("authPassword");
    password.value = "";
    password.placeholder = "New password";
    password.autocomplete = "new-password";

    document.getElementById("authConfirmPassword").value = "";

    modal.style.display = "flex";
    modal.style.zIndex = "10000";
    authMessage("Enter your new password.");
}

async function logoutUser() {
    if (!supabaseClient) {
        return;
    }

    const { error } =
        await supabaseClient.auth.signOut();

    if (error) {
        console.error(
            "Logout failed:",
            error
        );
        return;
    }

    currentSession = null;
    currentUser = null;
    __addUserMessageImpl = null;
    __addBotMessageImpl = null;
    await refreshAuthUI();
}

async function getCurrentSession() {
    if (!supabaseClient) {
        return null;
    }

    const {
        data: { session }
    } = await supabaseClient.auth.getSession();

    currentSession = session || null;
    currentUser = session?.user || null;

    return currentSession;
}

async function getAuthHeaders() {
    const session =
        await getCurrentSession();

    if (!session?.access_token) {
        return {};
    }

    return {
        Authorization:
            `Bearer ${session.access_token}`
    };
}

window.auth = {
    signUp: signUpWithEmail,
    login: loginWithEmail,
    googleLogin: loginWithGoogle,
    forgotPassword: sendPasswordResetEmail,
    updatePassword,
    logout: logoutUser,
    getSession: getCurrentSession,
    getHeaders: getAuthHeaders,
    getUser: () => currentUser
};


/* =====================================================
   INIT
   ===================================================== */

document.addEventListener(
    "DOMContentLoaded",
    () => {

        initBackground();

        createAuthUI();
        initSupabaseAuth();


        const toggle =
            document.getElementById(
                "themeToggle"
            );


        if (toggle) {

            toggle.addEventListener(
                "click",
                () => {

                    const current =
                        document.documentElement
                            .getAttribute(
                                "data-theme"
                            );


                    applyTheme(
                        current === "dark"
                            ? "light"
                            : "dark"
                    );
                }
            );
        }


        checkBackend();

        setInterval(
            checkBackend,
            15000
        );


        if (
            document.getElementById(
                "chatMessages"
            )
        ) {

            initChat();
        }

        // Offer account creation after a short guest period, while
        // keeping the chatbot fully usable without an account.
        setTimeout(showGuestAuthPrompt, 5000);
    }
);


/* =====================================================
   BACKEND STATUS
   ===================================================== */

async function checkBackend() {

    const status =
        document.getElementById(
            "status"
        );

    const text =
        document.getElementById(
            "statusText"
        );


    if (!status) {
        return;
    }


    try {

        const res =
            await fetch(
                STATUS_URL,
                {
                    method: "GET"
                }
            );


        if (!res.ok) {
            throw new Error(
                "bad status"
            );
        }


        status.className =
            "status online";


        text.textContent =
            "Backend Online";

    } catch (err) {

        status.className =
            "status offline";


        text.textContent =
            "Backend Offline";
    }
}


/* =====================================================
   GLOBAL CHAT MESSAGE BRIDGE
   ===================================================== */

let __addUserMessageImpl = null;
let __addBotMessageImpl = null;

function addUserMessage(message) {
    if (typeof __addUserMessageImpl === "function") {
        return __addUserMessageImpl(message);
    }
    console.warn("Chat UI is not initialized yet.");
}

function addBotMessage(message, route = "", isError = false, sources = []) {
    if (typeof __addBotMessageImpl === "function") {
        return __addBotMessageImpl(message, route, isError, sources);
    }
    console.warn("Chat UI is not initialized yet.");
}

/* =====================================================
   CONVERSATIONS / CHAT HISTORY
   ===================================================== */

async function getJSON(url, options = {}) {
    const headers = await getRequestHeaders(
        options.headers || {}
    );

    const response = await fetch(url, {
        ...options,
        headers
    });

    let data = null;

    try {
        data = await response.json();
    } catch (_) {
        data = null;
    }

    if (!response.ok) {
        throw new Error(
            data?.detail ||
            data?.message ||
            `HTTP error: ${response.status}`
        );
    }

    return data;
}


async function loadConversations() {
    const chatList =
        document.getElementById("chatList");

    if (!chatList) return;

    try {
        const data =
            await getJSON(CONVERSATIONS_URL);

        conversations =
            data.conversations || [];

        renderConversationList();

        if (
            !currentConversationId &&
            conversations.length
        ) {
            await openConversation(
                conversations[0].id
            );
        }

    } catch (error) {
        console.error(
            "Unable to load conversations:",
            error
        );
    }
}


function renderConversationList() {
    const chatList =
        document.getElementById("chatList");

    if (!chatList) return;

    chatList.innerHTML = "";

    conversations.forEach(
        (conversation) => {

            const item =
                document.createElement("li");

            item.className =
                "chat-item" +
                (
                    conversation.id ===
                    currentConversationId
                        ? " active"
                        : ""
                );

            item.dataset.conversationId =
                conversation.id;

            item.textContent =
                conversation.title ||
                "New Chat";

            chatList.appendChild(item);
        }
    );
}


async function createNewConversation() {
    try {
        const data =
            await getJSON(
                CONVERSATIONS_URL,
                {
                    method: "POST",
                    headers: {
                        "Content-Type":
                            "application/json"
                    },
                    body: JSON.stringify({
                        title: "New Chat"
                    })
                }
            );

        const conversation =
            data.conversation;

        if (!conversation) {
            throw new Error(
                "Conversation was not returned by backend."
            );
        }

        currentConversationId =
            conversation.id;

        conversations = [
            conversation,
            ...conversations.filter(
                (item) =>
                    item.id !== conversation.id
            )
        ];

        renderConversationList();
        resetChatUI();

        return conversation;

    } catch (error) {
        console.error(
            "Create conversation failed:",
            error
        );

        alert(
            error.message ||
            "Unable to create conversation."
        );

        return null;
    }
}


async function openConversation(
    conversationId
) {
    if (!conversationId) return;

    try {
        const data =
            await getJSON(
                CONVERSATION_MESSAGES_URL(
                    conversationId
                )
            );

        currentConversationId =
            conversationId;

        renderConversationList();
        resetChatUI();

        (data.messages || []).forEach(
            (message) => {

                const role =
                    String(
                        message.role || ""
                    ).toUpperCase();

                if (role === "USER") {
                    addUserMessage(
                        message.content || ""
                    );
                }

                if (role === "ASSISTANT") {
                    addBotMessage(
                        message.content || "",
                        message.route || "",
                        false,
                        message.metadata?.sources || []
                    );
                }
            }
        );

    } catch (error) {
        console.error(
            "Load conversation failed:",
            error
        );

        alert(
            error.message ||
            "Unable to load conversation."
        );
    }
}


function resetChatUI() {
    const chatInner =
        document.getElementById("chatInner");

    if (!chatInner) return;

    chatInner.innerHTML = `
        <div class="hero-empty">
            <div class="hero-logo">
                <img
                    src="/frontend/assets/logo/logo.svg"
                    width="52"
                    height="52"
                    alt=""
                >
            </div>

            <h2>Hello! I'm your Agentic AI assistant.</h2>

            <p>
                Ask me a question and I'll decide
                which specialized route should handle it.
            </p>

            <div class="chips">
                <button
                    class="chip"
                    data-prompt="Explain how neural networks learn"
                >
                    <span class="dot dot-direct"></span>
                    Explain how neural networks learn
                    <em>DIRECT</em>
                </button>

                <button
                    class="chip"
                    data-prompt="Who was Alan Turing?"
                >
                    <span class="dot dot-wikipedia"></span>
                    Who was Alan Turing?
                    <em>WIKIPEDIA</em>
                </button>

                <button
                    class="chip"
                    data-prompt="What is the latest AI news today?"
                >
                    <span class="dot dot-tavily"></span>
                    What is the latest AI news today?
                    <em>TAVILY</em>
                </button>

                <button
                    class="chip"
                    data-prompt="Find research papers about RAG"
                >
                    <span class="dot dot-arxiv"></span>
                    Find research papers about RAG
                    <em>ARXIV</em>
                </button>
            </div>
        </div>
    `;
}


async function refreshConversationList() {
    await loadConversations();
}


/* =====================================================
   CHAT
   ===================================================== */


/* =====================================================
   VOICE SEARCH / VOICE INPUT
   ===================================================== */

function getSupportedAudioMimeType() {
    const types = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
        "audio/mp4"
    ];

    if (!window.MediaRecorder) return "";

    for (const type of types) {
        if (MediaRecorder.isTypeSupported(type)) {
            return type;
        }
    }

    return "";
}

async function transcribeRecordedAudio(blob) {
    const formData = new FormData();

    const extension =
        blob.type.includes("ogg")
            ? "ogg"
            : blob.type.includes("mp4")
                ? "mp4"
                : "webm";

    formData.append(
        "file",
        blob,
        `voice-${Date.now()}.${extension}`
    );

    const response = await fetch(
        VOICE_TRANSCRIBE_URL,
        {
            method: "POST",
            headers: await getRequestHeaders(),
            body: formData
        }
    );

    let data = null;

    try {
        data = await response.json();
    } catch (_) {
        data = null;
    }

    if (!response.ok) {
        throw new Error(
            data?.detail ||
            data?.message ||
            `Voice transcription failed: HTTP ${response.status}`
        );
    }

    const text =
        data?.text ||
        data?.transcription ||
        data?.transcript ||
        "";

    if (!text.trim()) {
        throw new Error("No speech was detected.");
    }

    return text.trim();
}

async function startVoiceRecording(voiceButton, userInput) {
    if (!navigator.mediaDevices?.getUserMedia) {
        alert("Microphone recording is not supported by this browser.");
        return;
    }

    if (!window.MediaRecorder) {
        alert("MediaRecorder is not supported by this browser.");
        return;
    }

    try {
        voiceStream =
            await navigator.mediaDevices.getUserMedia({
                audio: true
            });

        voiceChunks = [];

        const mimeType =
            getSupportedAudioMimeType();

        mediaRecorder = mimeType
            ? new MediaRecorder(
                voiceStream,
                { mimeType }
            )
            : new MediaRecorder(voiceStream);

        mediaRecorder.addEventListener(
            "dataavailable",
            (event) => {
                if (event.data?.size > 0) {
                    voiceChunks.push(event.data);
                }
            }
        );

        mediaRecorder.addEventListener(
            "stop",
            async () => {
                const recordedType =
                    mediaRecorder?.mimeType ||
                    mimeType ||
                    "audio/webm";

                const blob =
                    new Blob(
                        voiceChunks,
                        { type: recordedType }
                    );

                voiceChunks = [];

                if (voiceStream) {
                    voiceStream
                        .getTracks()
                        .forEach(
                            (track) => track.stop()
                        );
                }

                voiceStream = null;

                try {
                    voiceButton.disabled = true;
                    voiceButton.classList.remove("recording");
                    voiceButton.classList.add("processing");
                    voiceButton.title = "Transcribing...";

                    const text =
                        await transcribeRecordedAudio(blob);

                    userInput.value = text;
                    userInput.focus();

                    userInput.dispatchEvent(
                        new Event("input", { bubbles: true })
                    );
                } catch (error) {
                    console.error(
                        "Voice transcription error:",
                        error
                    );

                    alert(
                        error.message ||
                        "Unable to transcribe voice."
                    );
                } finally {
                    voiceButton.disabled = false;
                    voiceButton.classList.remove("processing");
                    voiceButton.title = "Search by voice";
                    isRecordingVoice = false;
                    mediaRecorder = null;
                }
            }
        );

        mediaRecorder.start();
        isRecordingVoice = true;

        voiceButton.classList.add("recording");
        voiceButton.title = "Recording... Click to stop";

    } catch (error) {
        console.error(
            "Microphone access error:",
            error
        );

        if (error?.name === "NotAllowedError") {
            alert(
                "Microphone permission was denied. Please allow microphone access in Chrome."
            );
        } else {
            alert(
                error?.message ||
                "Unable to access microphone."
            );
        }

        if (voiceStream) {
            voiceStream
                .getTracks()
                .forEach(
                    (track) => track.stop()
                );
        }

        voiceStream = null;
        isRecordingVoice = false;
    }
}

function stopVoiceRecording() {
    if (
        mediaRecorder &&
        mediaRecorder.state !== "inactive"
    ) {
        mediaRecorder.stop();
    }
}

function createVoiceButton(inputContainer, userInput) {
    if (!inputContainer) {
        return null;
    }

    let button =
        document.getElementById("voiceSearchButton");

    if (button) {
        // The microphone button is now present in index.html.
        // Bind the click handler here instead of creating another button.
        if (!button.dataset.voiceBound) {
            button.dataset.voiceBound = "true";

            button.addEventListener(
                "click",
                async () => {
                    if (isRecordingVoice) {
                        stopVoiceRecording();
                        return;
                    }

                    await startVoiceRecording(
                        button,
                        userInput
                    );
                }
            );
        }

        return button;
    }

    button =
        document.createElement("button");

    button.id = "voiceSearchButton";
    button.type = "button";
    button.className = "voice-search-button";
    button.title = "Search by voice";
    button.setAttribute(
        "aria-label",
        "Search by voice"
    );

    button.innerHTML = `
        <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
        >
            <rect
                x="9"
                y="2"
                width="6"
                height="12"
                rx="3"
            ></rect>
            <path d="M5 10a7 7 0 0 0 14 0"></path>
            <line x1="12" y1="19" x2="12" y2="22"></line>
            <line x1="8" y1="22" x2="16" y2="22"></line>
        </svg>
    `;

    if (!button.dataset.voiceBound) {
        button.dataset.voiceBound = "true";

        button.addEventListener(
            "click",
            async () => {
                if (isRecordingVoice) {
                    stopVoiceRecording();
                    return;
                }

                await startVoiceRecording(
                    button,
                    userInput
                );
            }
        );
    }

    const sendButton =
        document.getElementById("sendButton");

    if (
        sendButton &&
        sendButton.parentElement === inputContainer
    ) {
        inputContainer.insertBefore(
            button,
            sendButton
        );
    } else {
        inputContainer.appendChild(button);
    }

    return button;
}

function initChat() {

    const userInput =
        document.getElementById(
            "userInput"
        );

    const sendButton =
        document.getElementById(
            "sendButton"
        );

    const chatMessages =
        document.getElementById(
            "chatMessages"
        );

    const chatInner =
        document.getElementById(
            "chatInner"
        );

    const sidebar =
        document.getElementById(
            "sidebar"
        );

    const overlay =
        document.getElementById(
            "overlay"
        );

    const menuButton =
        document.getElementById(
            "menuButton"
        );

    const newChatButton =
        document.getElementById(
            "newChatButton"
        );

    const chatList =
        document.getElementById(
            "chatList"
        );

    const inputContainer =
        sendButton?.parentElement;

    createVoiceButton(
        inputContainer,
        userInput
    );

    if (!document.getElementById("voiceSearchStyles")) {
        const voiceStyle =
            document.createElement("style");

        voiceStyle.id =
            "voiceSearchStyles";

        voiceStyle.textContent = `
            .voice-search-button {
                width: 44px;
                height: 44px;
                border: 1px solid rgba(99,102,241,.35);
                border-radius: 12px;
                background: transparent;
                color: inherit;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                cursor: pointer;
                flex-shrink: 0;
                transition: transform .15s ease, background .15s ease;
            }

            .voice-search-button:hover {
                transform: translateY(-1px);
                background: rgba(99,102,241,.10);
            }

            .voice-search-button.recording {
                background: rgba(239,68,68,.14);
                border-color: rgba(239,68,68,.65);
                color: #ef4444;
                animation: voicePulse 1.1s infinite;
            }

            .voice-search-button.processing {
                opacity: .65;
                cursor: wait;
            }

            @keyframes voicePulse {
                0%, 100% { transform: scale(1); }
                50% { transform: scale(1.08); }
            }
        `;

        document.head.appendChild(voiceStyle);
    }


    document.body.classList.add(
        "has-sidebar"
    );


    const welcomeHTML =
        chatInner.innerHTML;


    /* =================================================
       HELPERS
       ================================================= */

    function scrollToBottom() {

        chatMessages.scrollTop =
            chatMessages.scrollHeight;
    }


    function escapeHTML(str) {

        return String(str)
            .replace(
                /&/g,
                "&amp;"
            )
            .replace(
                /</g,
                "&lt;"
            )
            .replace(
                />/g,
                "&gt;"
            );
    }


    function timeNow() {

        return new Date()
            .toLocaleTimeString(
                [],
                {
                    hour: "2-digit",
                    minute: "2-digit"
                }
            );
    }


    /* =================================================
       MARKDOWN RENDERER
       ================================================= */

    function renderMarkdown(src) {

        const blocks = [];


        let text =
            escapeHTML(
                src || ""
            );


        /* Code blocks */

        text = text.replace(
            /```(?:\w+)?\n?([\s\S]*?)```/g,
            (_, code) => {

                blocks.push(
                    `<pre><code>${code.trim()}</code></pre>`
                );

                return `@@BLOCK${blocks.length - 1}@@`;
            }
        );


        const inline = (s) => {

            return s

                /* Inline code */
                .replace(
                    /`([^`]+)`/g,
                    "<code>$1</code>"
                )

                /* Bold */
                .replace(
                    /\*\*([^*]+)\*\*/g,
                    "<strong>$1</strong>"
                )

                /* Italic */
                .replace(
                    /(^|[^*])\*([^*\n]+)\*/g,
                    "$1<em>$2</em>"
                )

                /* Markdown links */
                .replace(
                    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
                    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
                )

                /* Plain URLs */
                .replace(
                    /(^|[\s(])(https?:\/\/[^\s<)]+)/g,
                    '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>'
                );
        };


        const out = [];

        let list = null;


        const closeList = () => {

            if (list) {

                out.push(
                    `</${list}>`
                );

                list = null;
            }
        };


        text.split("\n")
            .forEach((line) => {

                const ul =
                    line.match(
                        /^\s*[-*]\s+(.*)/
                    );


                const ol =
                    line.match(
                        /^\s*\d+\.\s+(.*)/
                    );


                const h =
                    line.match(
                        /^(#{1,3})\s+(.*)/
                    );


                if (ul || ol) {

                    const tag =
                        ul ? "ul" : "ol";


                    if (list !== tag) {

                        closeList();

                        out.push(
                            `<${tag}>`
                        );

                        list = tag;
                    }


                    out.push(
                        `<li>${inline(
                            (ul || ol)[1]
                        )}</li>`
                    );

                } else {

                    closeList();


                    if (h) {

                        out.push(
                            `<h3>${inline(
                                h[2]
                            )}</h3>`
                        );

                    } else if (
                        line.trim() === ""
                    ) {

                        out.push("");

                    } else if (
                        /^@@BLOCK\d+@@$/.test(
                            line.trim()
                        )
                    ) {

                        out.push(
                            line.trim()
                        );

                    } else {

                        out.push(
                            `<p>${inline(
                                line
                            )}</p>`
                        );
                    }
                }
            });


        closeList();


        return out
            .join("")
            .replace(
                /@@BLOCK(\d+)@@/g,
                (_, i) => blocks[i]
            );
    }


    /* =================================================
       HERO
       ================================================= */

    function removeHero() {

        const hero =
            chatInner.querySelector(
                ".hero-empty"
            );


        if (hero) {
            hero.remove();
        }
    }


    /* =================================================
       CREATE MESSAGE
       ================================================= */

    function createMessage(type) {

        const wrap =
            document.createElement(
                "div"
            );


        wrap.className =
            `message ${
                type === "user"
                    ? "user-message"
                    : "bot-message"
            }`;


        const avatar =
            document.createElement(
                "div"
            );


        avatar.className =
            "avatar";


        avatar.textContent =
            type === "user"
                ? "You"
                : "AI";


        const content =
            document.createElement(
                "div"
            );


        content.className =
            "message-content";


        if (type === "user") {

            const name =
                document.createElement(
                    "div"
                );


            name.className =
                "message-name";


            name.innerHTML =
                `<span>You</span>` +
                `<span class="message-time">${timeNow()}</span>`;


            content.appendChild(
                name
            );
        }


        wrap.appendChild(
            avatar
        );

        wrap.appendChild(
            content
        );

        chatInner.appendChild(
            wrap
        );


        return {
            wrap,
            content
        };
    }


    /* =================================================
       USER MESSAGE
       ================================================= */

    function addUserMessage(message) {

        removeHero();


        const { content } =
            createMessage(
                "user"
            );


        const text =
            document.createElement(
                "div"
            );


        text.className =
            "message-text";


        text.textContent =
            message;


        content.appendChild(
            text
        );


        scrollToBottom();
    }

    __addUserMessageImpl = addUserMessage;


    /* =================================================
       BOT MESSAGE
       ================================================= */

    function addBotMessage(
        message,
        route = "",
        isError = false,
        sources = []
    ) {

        const { content } =
            createMessage(
                "bot"
            );


        const safeRoute =
            ROUTES.includes(route)
                ? route
                : "";


        const card =
            document.createElement(
                "div"
            );


        card.className =
            "answer-card" +
            (
                safeRoute
                    ? ` route-${safeRoute}`
                    : ""
            ) +
            (
                isError
                    ? " is-error"
                    : ""
            );


        const head =
            document.createElement(
                "div"
            );


        head.className =
            "answer-head";


        head.innerHTML =
            `<strong>AI Assistant</strong>` +

            (
                safeRoute
                    ? `<span class="route-badge route-${safeRoute}">${safeRoute}</span>`
                    : ""
            ) +

            `<span class="message-time">${timeNow()}</span>`;


        const body =
            document.createElement(
                "div"
            );


        body.className =
            "answer-body";


        if (isError) {

            body.innerHTML =
                `<div class="error-text">${escapeHTML(message)}</div>` +
                `<div class="error-sub">Please make sure the FastAPI server is running.</div>`;

        } else {

            body.innerHTML =
                renderMarkdown(
                    message
                );
        }


        [...body.children]
            .forEach(
                (el, i) => {

                    el.style.animationDelay =
                        `${Math.min(
                            i * 70,
                            700
                        )}ms`;
                }
            );


        card.appendChild(
            head
        );

        card.appendChild(
            body
        );


        /* =================================================
           SOURCES
           ================================================= */

        if (
            !isError &&
            sources.length > 0
        ) {

            const sourcesBox =
                document.createElement(
                    "div"
                );


            sourcesBox.className =
                "sources-box";


            const sourcesTitle =
                document.createElement(
                    "div"
                );


            sourcesTitle.className =
                "sources-title";


            sourcesTitle.textContent =
                "Sources";


            sourcesBox.appendChild(
                sourcesTitle
            );


            sources.forEach(
                (source) => {

                    if (!source.url) {
                        return;
                    }


                    const sourceLink =
                        document.createElement(
                            "a"
                        );


                    sourceLink.href =
                        source.url;


                    sourceLink.target =
                        "_blank";


                    sourceLink.rel =
                        "noopener noreferrer";


                    sourceLink.className =
                        "source-link";


                    sourceLink.textContent =
                        source.title ||
                        source.url;


                    sourcesBox.appendChild(
                        sourceLink
                    );
                }
            );


            card.appendChild(
                sourcesBox
            );
        }


        /* =================================================
           FOOTER
           ================================================= */

        if (!isError) {

            const foot =
                document.createElement(
                    "div"
                );


            foot.className =
                "answer-foot";


            const trace =
                document.createElement(
                    "div"
                );


            trace.className =
                "trace";


            trace.innerHTML =
                safeRoute

                    ? `<span class="step">User</span><i>→</i>` +
                      `<span class="step">Router</span><i>→</i>` +
                      `<span class="route-badge route-${safeRoute}">${safeRoute}</span>` +
                      `<i>→</i>` +
                      `<span class="step">Groq LLM</span>`

                    : "";


            const copy =
                document.createElement(
                    "button"
                );


            copy.className =
                "copy-btn";


            copy.type =
                "button";


            copy.textContent =
                "Copy";


            copy.addEventListener(
                "click",
                async () => {

                    try {

                        await navigator
                            .clipboard
                            .writeText(
                                message
                            );


                        copy.textContent =
                            "Copied ✓";

                    } catch (e) {

                        copy.textContent =
                            "Failed";
                    }


                    setTimeout(
                        () => {

                            copy.textContent =
                                "Copy";

                        },
                        1500
                    );
                }
            );


            foot.appendChild(
                trace
            );

            foot.appendChild(
                copy
            );


            card.appendChild(
                foot
            );
        }


        content.appendChild(
            card
        );


        scrollToBottom();
    }

    __addBotMessageImpl = addBotMessage;


    /* =================================================
       ROUTER ANIMATION
       ================================================= */

    function createRouterPanel() {

        const {
            wrap,
            content
        } = createMessage(
            "bot"
        );


        const panel =
            document.createElement(
                "div"
            );


        panel.className =
            "router-panel";


        panel.innerHTML = `

            <div class="router-bar"></div>

            <div class="router-head">

                <svg
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                >

                    <path
                        d="M13 2 3 14h9l-1 8 10-12h-9z"
                    />

                </svg>

                LangGraph Router

                <span class="state-done">
                    ✓ Route selected
                </span>

            </div>


            <div class="router-status">
                Analyzing your question
                <span class="dots">
                    <span>.</span>
                    <span>.</span>
                    <span>.</span>
                </span>
            </div>


            <div class="router-flow">

                <span class="node">
                    USER QUESTION
                </span>

                ↓

                <span class="node">
                    ROUTER
                </span>

                ↓

            </div>


            <div class="route-grid">

                ${ROUTES.map(
                    (r) => `

                    <div
                        class="route-card"
                        data-route="${r}"
                    >

                        <span class="marker"></span>

                        ${r}

                    </div>

                `
                ).join("")}

            </div>
        `;


        content.appendChild(
            panel
        );


        scrollToBottom();


        const statusEl =
            panel.querySelector(
                ".router-status"
            );


        const cards =
            [
                ...panel.querySelectorAll(
                    ".route-card"
                )
            ];


        const setStatus =
            (
                msg,
                dots = true
            ) => {

                statusEl.innerHTML =
                    msg +
                    (
                        dots

                            ? ` <span class="dots">` +
                              `<span>.</span>` +
                              `<span>.</span>` +
                              `<span>.</span>` +
                              `</span>`

                            : ""
                    );
            };


        let scanningIndex = 0;


        const startScanning = () => {

            cards.forEach(
                (c) =>
                    c.classList.remove(
                        "scanning"
                    )
            );


            if (cards.length > 0) {

                cards[
                    scanningIndex % cards.length
                ].classList.add(
                    "scanning"
                );

                scanningIndex++;
            }
        };


        const scanningInterval =
            setInterval(
                startScanning,
                380
            );


        return {
            wrap,
            panel,
            statusEl,
            cards,
            setStatus,
            scanningInterval
        };
    }


    /* =================================================
       UPDATE ROUTER
       ================================================= */

    async function resolveRouterAnimation(
        routerUI,
        route
    ) {

        const safeRoute =
            ROUTES.includes(route)
                ? route
                : "DIRECT";


        clearInterval(
            routerUI.scanningInterval
        );


        routerUI.cards.forEach(
            (card) => {

                card.classList.remove(
                    "scanning"
                );


                if (
                    card.dataset.route ===
                    safeRoute
                ) {

                    card.classList.add(
                        "selected"
                    );

                } else {

                    card.classList.add(
                        "faded"
                    );
                }
            }
        );


        routerUI.panel.classList.add(
            "resolved"
        );


        routerUI.setStatus(
            `Route selected: <strong>${escapeHTML(safeRoute)}</strong>`,
            false
        );


        await sleep(700);


        routerUI.setStatus(
            ROUTE_STATUS[safeRoute] ||
            "Processing...",
            false
        );


        await sleep(500);


        routerUI.wrap.remove();


        return safeRoute;
    }


    /* =================================================
       STREAMING CHAT

       IMPORTANT:
       This function performs the ONLY request.

       It receives:
       route
       content
       sources
       done
       error

       from /chat/stream.
       ================================================= */

    async function streamChat(
        query
    ) {

        removeHero();


        /* =================================================
           ROUTER UI
           ================================================= */

        const routerUI =
            createRouterPanel();


        /* =================================================
           CREATE RESPONSE CARD
           ================================================= */

        let responseMessage = null;

        let card = null;

        let body = null;

        let streamingText = null;


        let route =
            "";


        let fullResponse =
            "";


        let sources =
            [];


        let routeResolved =
            false;


        let responseCardCreated =
            false;


        /* =================================================
           FETCH STREAM
           ================================================= */

        const response =
            await fetch(
                STREAM_CHAT_URL,
                {
                    method: "POST",

                    headers: await getRequestHeaders({
                        "Content-Type": "application/json"
                    }),

                    body: JSON.stringify({
                        query: query,
                        conversation_id:
                            currentConversationId,
                        document_id:
                            selectedDocumentId,
                        image_path:
                            selectedImagePath
                    })
                }
            );


        if (!response.ok) {

            clearInterval(
                routerUI.scanningInterval
            );

            routerUI.wrap.remove();

            throw new Error(
                `HTTP error: ${response.status}`
            );
        }


        if (!response.body) {

            clearInterval(
                routerUI.scanningInterval
            );

            routerUI.wrap.remove();

            throw new Error(
                "Streaming is not supported by this browser."
            );
        }


        const reader =
            response.body.getReader();


        const decoder =
            new TextDecoder();


        let buffer =
            "";


        /* =================================================
           CREATE RESPONSE CARD
           ================================================= */

        function createStreamingMessage(
            selectedRoute
        ) {

            if (responseCardCreated) {
                return;
            }


            responseCardCreated =
                true;


            const {
                content
            } = createMessage(
                "bot"
            );


            responseMessage =
                content;


            const safeRoute =
                ROUTES.includes(
                    selectedRoute
                )
                    ? selectedRoute
                    : "";


            card =
                document.createElement(
                    "div"
                );


            card.className =
                "answer-card" +
                (
                    safeRoute
                        ? ` route-${safeRoute}`
                        : ""
                );


            /* =================================================
               HEADER
               ================================================= */

            const head =
                document.createElement(
                    "div"
                );


            head.className =
                "answer-head";


            head.innerHTML =
                `<strong>AI Assistant</strong>` +

                (
                    safeRoute
                        ? `<span class="route-badge route-${safeRoute}">${safeRoute}</span>`
                        : ""
                ) +

                `<span class="message-time">${timeNow()}</span>`;


            /* =================================================
               BODY
               ================================================= */

            body =
                document.createElement(
                    "div"
                );


            body.className =
                "answer-body";


            streamingText =
                document.createElement(
                    "div"
                );


            streamingText.className =
                "streaming-text";


            streamingText.textContent =
                "Generating response...";


            body.appendChild(
                streamingText
            );


            card.appendChild(
                head
            );


            card.appendChild(
                body
            );


            responseMessage.appendChild(
                card
            );


            scrollToBottom();
        }


        /* =================================================
           READ SSE STREAM
           ================================================= */

        try {

            while (true) {

                const {
                    value,
                    done
                } =
                    await reader.read();


                if (done) {
                    break;
                }


                buffer +=
                    decoder.decode(
                        value,
                        {
                            stream: true
                        }
                    );


                const events =
                    buffer.split(
                        "\n\n"
                    );


                buffer =
                    events.pop();


                for (
                    const event
                    of events
                ) {

                    if (
                        !event.startsWith(
                            "data:"
                        )
                    ) {
                        continue;
                    }


                    const jsonText =
                        event
                            .replace(
                                /^data:\s*/,
                                ""
                            )
                            .trim();


                    if (!jsonText) {
                        continue;
                    }


                    let data;


                    try {

                        data =
                            JSON.parse(
                                jsonText
                            );

                    } catch (error) {

                        console.error(
                            "Invalid stream data:",
                            jsonText
                        );

                        continue;
                    }


                    /* =========================================
                       ROUTE EVENT
                       ========================================= */

                    if (
                        data.type ===
                        "route"
                    ) {

                        route =
                            String(
                                data.route ||
                                ""
                            ).toUpperCase();


                        console.log(
                            "Route:",
                            route
                        );


                        if (
                            !routeResolved
                        ) {

                            routeResolved =
                                true;


                            await resolveRouterAnimation(
                                routerUI,
                                route
                            );


                            createStreamingMessage(
                                route
                            );
                        }


                        continue;
                    }


                    /* =========================================
                       CONTENT EVENT
                       ========================================= */

                    if (
                        data.type ===
                        "content"
                    ) {

                        if (
                            !responseCardCreated
                        ) {

                            createStreamingMessage(
                                route
                            );
                        }


                        fullResponse +=
                            data.content;


                        streamingText.innerHTML =
                            renderMarkdown(
                                fullResponse
                            );


                        scrollToBottom();

                        continue;
                    }


                    /* =========================================
                       SOURCES EVENT
                       ========================================= */

                    if (
                        data.type ===
                        "sources"
                    ) {

                        sources =
                            data.sources ||
                            [];


                        if (
                            sources.length > 0 &&
                            card
                        ) {

                            const oldSources =
                                card.querySelector(
                                    ".sources-box"
                                );


                            if (oldSources) {
                                oldSources.remove();
                            }


                            const sourcesBox =
                                document.createElement(
                                    "div"
                                );


                            sourcesBox.className =
                                "sources-box";


                            const sourcesTitle =
                                document.createElement(
                                    "div"
                                );


                            sourcesTitle.className =
                                "sources-title";


                            sourcesTitle.textContent =
                                "Sources";


                            sourcesBox.appendChild(
                                sourcesTitle
                            );


                            sources.forEach(
                                (source) => {

                                    if (
                                        !source.url
                                    ) {
                                        return;
                                    }


                                    const sourceLink =
                                        document.createElement(
                                            "a"
                                        );


                                    sourceLink.href =
                                        source.url;


                                    sourceLink.target =
                                        "_blank";


                                    sourceLink.rel =
                                        "noopener noreferrer";


                                    sourceLink.className =
                                        "source-link";


                                    sourceLink.textContent =
                                        source.title ||
                                        source.url;


                                    sourcesBox.appendChild(
                                        sourceLink
                                    );
                                }
                            );


                            card.appendChild(
                                sourcesBox
                            );
                        }


                        scrollToBottom();

                        continue;
                    }


                    /* =========================================
                       ERROR EVENT
                       ========================================= */

                    if (
                        data.type ===
                        "error"
                    ) {

                        throw new Error(
                            data.content ||
                            "Streaming error"
                        );
                    }


                    /* =========================================
                       DONE EVENT
                       ========================================= */

                    if (
                        data.type ===
                        "done"
                    ) {

                        console.log(
                            "Streaming completed"
                        );

                        setTimeout(() => {
                            refreshConversationList();
                        }, 150);
                    }
                }
            }

        } catch (error) {

            clearInterval(
                routerUI.scanningInterval
            );


            if (
                routerUI.wrap &&
                routerUI.wrap.parentNode
            ) {

                routerUI.wrap.remove();
            }


            throw error;
        }


        /* =================================================
           FINAL RESPONSE CARD
           ================================================= */

        if (
            !responseCardCreated
        ) {

            createStreamingMessage(
                route
            );
        }


        /* =================================================
           FINAL FOOTER
           ================================================= */

        const foot =
            document.createElement(
                "div"
            );


        foot.className =
            "answer-foot";


        const trace =
            document.createElement(
                "div"
            );


        trace.className =
            "trace";


        const safeRoute =
            ROUTES.includes(route)
                ? route
                : "";


        trace.innerHTML =
            safeRoute

                ? `<span class="step">User</span>` +
                  `<i>→</i>` +
                  `<span class="step">Router</span>` +
                  `<i>→</i>` +
                  `<span class="route-badge route-${safeRoute}">${safeRoute}</span>` +
                  `<i>→</i>` +
                  `<span class="step">Groq LLM</span>`

                : "";


        const copy =
            document.createElement(
                "button"
            );


        copy.className =
            "copy-btn";


        copy.type =
            "button";


        copy.textContent =
            "Copy";


        copy.addEventListener(
            "click",
            async () => {

                try {

                    await navigator
                        .clipboard
                        .writeText(
                            fullResponse
                        );


                    copy.textContent =
                        "Copied ✓";

                } catch (e) {

                    copy.textContent =
                        "Failed";
                }


                setTimeout(
                    () => {

                        copy.textContent =
                            "Copy";

                    },
                    1500
                );
            }
        );


        foot.appendChild(
            trace
        );


        foot.appendChild(
            copy
        );


        card.appendChild(
            foot
        );


        scrollToBottom();


        return {
            response:
                fullResponse,

            sources:
                sources,

            route:
                safeRoute
        };
    }


    /* =================================================
       SEND MESSAGE

       IMPORTANT:
       Only /chat/stream is called.

       The route is received from the SAME
       streaming request.

       There is NO /chat request here.
       ================================================= */

    async function sendMessage() {

        const query =
            userInput.value.trim();


        if (
            !query ||
            sendButton.disabled
        ) {
            return;
        }

        if (!currentConversationId) {
            const conversation =
                await createNewConversation();

            if (!conversation) {
                return;
            }
        }


        addUserMessage(
            query
        );


        userInput.value = "";

        userInput.style.height =
            "auto";


        sendButton.disabled =
            true;


        try {

            /* =============================================
               SINGLE REQUEST

               /chat/stream performs:

               1. Router
               2. Selected tool
               3. Groq LLM
               4. Streaming
               5. Sources
               ============================================= */

            await streamChat(
                query
            );

        } catch (error) {

            console.error(
                "Chat error:",
                error
            );


            addBotMessage(
                "Unable to connect to the AI backend.",
                "",
                true
            );

        } finally {

            sendButton.disabled =
                false;


            userInput.focus();
        }
    }


    /* =================================================
       INPUT EVENTS
       ================================================= */

    sendButton.addEventListener(
        "click",
        sendMessage
    );


    userInput.addEventListener(
        "keydown",
        (e) => {

            if (
                e.key === "Enter" &&
                !e.shiftKey
            ) {

                e.preventDefault();

                sendMessage();
            }
        }
    );


    userInput.addEventListener(
        "input",
        () => {

            userInput.style.height =
                "auto";


            userInput.style.height =
                Math.min(
                    userInput.scrollHeight,
                    160
                ) + "px";
        }
    );


    /* =================================================
       SUGGESTION CHIPS
       ================================================= */

    chatInner.addEventListener(
        "click",
        (e) => {

            const chip =
                e.target.closest(
                    ".chip"
                );


            if (!chip) {
                return;
            }


            userInput.value =
                chip.dataset.prompt;


            sendMessage();
        }
    );


    /* =================================================
       SIDEBAR
       ================================================= */

    const isMobile =
        () =>
            window.matchMedia(
                "(max-width: 800px)"
            ).matches;


    menuButton.addEventListener(
        "click",
        () => {

            if (isMobile()) {

                sidebar.classList.toggle(
                    "open"
                );


                overlay.classList.toggle(
                    "show"
                );

            } else {

                sidebar.classList.toggle(
                    "collapsed"
                );
            }
        }
    );


    overlay.addEventListener(
        "click",
        () => {

            sidebar.classList.remove(
                "open"
            );


            overlay.classList.remove(
                "show"
            );
        }
    );


    /* =================================================
       NEW CHAT
       ================================================= */

    newChatButton.addEventListener(
        "click",
        async () => {

            const conversation =
                await createNewConversation();

            if (!conversation) {
                return;
            }

            sidebar.classList.remove("open");
            overlay.classList.remove("show");
            userInput.focus();
        }
    );


    /* =================================================
       CHAT LIST
       ================================================= */

    chatList.addEventListener(
        "click",
        async (e) => {

            const item =
                e.target.closest(
                    ".chat-item"
                );

            if (!item) {
                return;
            }

            const conversationId =
                item.dataset.conversationId;

            if (!conversationId) {
                return;
            }

            await openConversation(
                conversationId
            );

            sidebar.classList.remove("open");
            overlay.classList.remove("show");
        }
    );


    userInput.focus();
}

/* =====================================================
   FILE UPLOAD
   ===================================================== */
function clearSelectedFile() {
    pendingFileAfterLogin = null;
    pendingFileNeedsLogin = false;
    selectedDocumentId = null;
    selectedImagePath = null;
    selectedFileName = "";
    selectedFileType = "";
    selectedFileUploading = false;
    const input = document.getElementById("fileInput");
    const preview = document.getElementById("filePreview");
    const button = document.getElementById("attachFileButton");
    if (input) input.value = "";
    if (button) button.disabled = false;
    if (preview) {
        preview.className = "file-preview";
        preview.innerHTML = "";
    }
}

function renderFilePreview(file, statusText = "Uploading...") {
    const preview = document.getElementById("filePreview");
    if (!preview) return;
    const isPDF = file?.name?.toLowerCase().endsWith(".pdf");
    const icon = isPDF ? "📄" : "🖼️";
    preview.className = "file-preview active";
    preview.innerHTML = `
        <div class="file-preview-left">
            <span class="file-preview-icon">${icon}</span>
            <div>
                <div class="file-preview-name">${escapeHTML(file?.name || "File")}</div>
                <div id="filePreviewStatus" class="file-preview-status">${escapeHTML(statusText)}</div>
            </div>
        </div>
        <button id="removeSelectedFile" class="file-remove-button" type="button" title="Remove file" aria-label="Remove file">✕</button>
    `;
    document.getElementById("removeSelectedFile")?.addEventListener("click", clearSelectedFile);
}

function updateFilePreviewStatus(text, state = "") {
    const status = document.getElementById("filePreviewStatus");
    if (!status) return;
    status.textContent = text;
    status.className = "file-preview-status" + (state ? ` ${state}` : "");
}

async function uploadSelectedFile(file) {
    if (!file) return;

    const isPDF = file.name.toLowerCase().endsWith(".pdf");

    // Defensive check: the upload button already blocks logged-out users,
    // but keep the backend-aligned guard here as well.
    if (!currentUser) {
        document.getElementById("authLoginButton")?.click();
        return;
    }

    const attachButton = document.getElementById("attachFileButton");
    pendingFileAfterLogin = null;
    pendingFileNeedsLogin = false;
    selectedDocumentId = null;
    selectedImagePath = null;
    selectedFileName = file.name;
    selectedFileType = file.type || "";
    selectedFileUploading = true;
    if (attachButton) attachButton.disabled = true;
    renderFilePreview(file, "Uploading...");
    const formData = new FormData();
    formData.append("file", file);
    const endpoint = isPDF ? PDF_UPLOAD_URL : IMAGE_UPLOAD_URL;
    try {
        const requestHeaders = isPDF
            ? await getAuthHeaders()
            : await getRequestHeaders();

        if (isPDF && !requestHeaders.Authorization) {
            throw new Error("Login is required to upload a PDF.");
        }

        const response = await fetch(endpoint, {
            method: "POST",
            headers: requestHeaders,
            body: formData
        });
        let data = null;
        try { data = await response.json(); } catch (_) { data = null; }
        if (!response.ok) {
            throw new Error(data?.detail || `Upload failed. HTTP ${response.status}`);
        }
        if (isPDF) {
            if (!data?.document_id) throw new Error("PDF uploaded but document_id was not returned.");
            selectedDocumentId = data.document_id;
            selectedImagePath = null;
            updateFilePreviewStatus("PDF ready for RAG", "ready");
        } else {
            if (!data?.image_path) throw new Error("Image uploaded but image_path was not returned.");
            selectedImagePath = data.image_path;
            selectedDocumentId = null;
            updateFilePreviewStatus("Image ready for analysis", "ready");
        }
        selectedFileUploading = false;
        if (attachButton) attachButton.disabled = false;
    } catch (error) {
        console.error("File upload error:", error);
        selectedDocumentId = null;
        selectedImagePath = null;
        selectedFileUploading = false;
        updateFilePreviewStatus(error?.message || "File upload failed.", "error");
        if (attachButton) attachButton.disabled = false;
    }
}

function initFileUpload() {
    const attachButton = document.getElementById("attachFileButton");
    const fileInput = document.getElementById("fileInput");
    if (!attachButton || !fileInput || attachButton.dataset.fileUploadBound) return;
    attachButton.dataset.fileUploadBound = "true";
    attachButton.addEventListener("click", () => {
        if (selectedFileUploading) {
            return;
        }

        // Upload always starts with authentication.
        // The user must login before the file picker is opened.
        if (!currentUser) {
            document.getElementById("authLoginButton")?.click();
            return;
        }

        fileInput.click();
    });
    fileInput.addEventListener("change", async (event) => {
        const file = event.target.files?.[0];
        if (!file) return;
        const isPDF = file.name.toLowerCase().endsWith(".pdf");
        const maxSize = isPDF ? 25 * 1024 * 1024 : 10 * 1024 * 1024;
        if (file.size > maxSize) {
            alert(isPDF ? "Maximum PDF size is 25 MB." : "Maximum image size is 10 MB.");
            fileInput.value = "";
            return;
        }
        if (!isPDF && !["image/jpeg", "image/png", "image/webp", "image/bmp"].includes(file.type)) {
            alert("Please select a PDF or supported image.");
            fileInput.value = "";
            return;
        }
        await uploadSelectedFile(file);
    });
}

document.addEventListener("DOMContentLoaded", initFileUpload);
