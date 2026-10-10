/* =====================================================
   Sign-in logic shared by the login dialog (no DOM).

   - describeAuthError: turns a Supabase Auth error into a message,
     keeping network problems apart from wrong credentials.
   - OtpFlow: passwordless sign-in with an emailed one-time code.
     Supabase Auth issues, expires and verifies every code (Dashboard:
     Authentication → Email → "Email OTP expiration"); this object only
     tracks what the dialog shows. The expiry/resend values it receives
     are for countdowns and never decide whether a code is accepted.

   Loaded before common.js. Also runs under Node for unit tests
   (frontend/tests/auth-flows.test.js).
   ===================================================== */

(function (root) {
    "use strict";

    const AUTH_MESSAGES = {
        network: "We couldn't reach the sign-in service. Check your connection and try again.",
        rateLimited: "Too many attempts. Please wait a minute and try again.",
        invalidCredentials: "Incorrect email or password.",
        emailNotConfirmed: "Please confirm your email address first, using the email we sent you.",
        codeInvalid: "That code is incorrect or has expired. Use the code from the latest email, or request a new one.",
        codeExpired: "This code has expired. Request a new code.",
        sendFailed: "We couldn't send the email right now. Please try again in a few minutes.",
        signupDisabled: "New sign-ups are currently disabled.",
        badEmail: "Please enter a valid email address.",
        badCode: "Enter the code from the email (digits only).",
        noEmail: "Request a code first.",
        generic: "Sign-in failed. Please try again.",
    };

    const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    // Supabase sends 6 digits by default; the length is configurable (6–10).
    const CODE_RE = /^\d{6,10}$/;

    function isNetworkError(error) {
        return error instanceof TypeError
            || error?.name === "AuthRetryableFetchError"
            || error?.status === 0
            || [502, 503, 504].includes(error?.status);
    }

    /** User-facing message for a Supabase Auth error. Provider details
     *  are never shown beyond Supabase's own short public messages. */
    function describeAuthError(error) {
        if (!error) return null;
        const code = error.code || "";
        const status = error.status;

        if (isNetworkError(error)) return AUTH_MESSAGES.network;
        if (status === 429 || /rate_limit/.test(code)) return AUTH_MESSAGES.rateLimited;
        if (code === "invalid_credentials") return AUTH_MESSAGES.invalidCredentials;
        if (code === "email_not_confirmed") return AUTH_MESSAGES.emailNotConfirmed;
        if (code === "otp_expired") return AUTH_MESSAGES.codeInvalid;
        if (code === "signup_disabled" || code === "otp_disabled") return AUTH_MESSAGES.signupDisabled;
        if (code === "email_address_invalid") return AUTH_MESSAGES.badEmail;
        if (status >= 500) {
            return /email/i.test(error.message || "") ? AUTH_MESSAGES.sendFailed : AUTH_MESSAGES.generic;
        }
        // Remaining 4xx (weak password, already registered, ...): Supabase's
        // short public message is the most useful thing to show.
        const message = typeof error.message === "string" ? error.message.trim() : "";
        return message && message.length <= 160 ? message : AUTH_MESSAGES.generic;
    }

    class OtpFlow {
        /**
         * auth: the Supabase client's `auth` object.
         * expirySeconds / resendSeconds: display values from /config.
         */
        constructor({ auth, expirySeconds = 600, resendSeconds = 60, redirectTo = null, now = () => Date.now() }) {
            this.auth = auth;
            this.expirySeconds = expirySeconds;
            this.resendSeconds = resendSeconds;
            this.redirectTo = redirectTo;
            this.now = now;
            this.email = "";
            this.sentAt = null;
            this.cooldownUntil = 0;
            this.busy = false;
        }

        get secondsUntilResend() {
            return Math.max(0, Math.ceil((this.cooldownUntil - this.now()) / 1000));
        }

        get secondsUntilExpiry() {
            if (this.sentAt === null) return 0;
            return Math.max(0, Math.ceil((this.sentAt + this.expirySeconds * 1000 - this.now()) / 1000));
        }

        /** Shown to the user only; Supabase still decides on verify. */
        get expired() {
            return this.sentAt !== null && this.secondsUntilExpiry === 0;
        }

        /** Send a code to `email`. Also creates the account if it is new,
         *  so the response is the same for registered and unknown emails. */
        async request(email) {
            if (this.busy) return { ok: false, busy: true };
            const value = String(email || "").trim();
            if (!EMAIL_RE.test(value)) return { ok: false, message: AUTH_MESSAGES.badEmail };

            const sameEmail = value.toLowerCase() === this.email.toLowerCase();
            if (sameEmail && this.secondsUntilResend > 0) {
                return { ok: false, message: `Please wait ${this.secondsUntilResend} s before requesting another code.` };
            }

            this.busy = true;
            try {
                const { error } = await this.auth.signInWithOtp({
                    email: value,
                    options: {
                        shouldCreateUser: true,
                        ...(this.redirectTo ? { emailRedirectTo: this.redirectTo } : {}),
                    },
                });
                if (error) {
                    if (error.status === 429) this.cooldownUntil = this.now() + this.resendSeconds * 1000;
                    return { ok: false, message: describeAuthError(error) };
                }
                this.email = value;
                this.sentAt = this.now();
                this.cooldownUntil = this.sentAt + this.resendSeconds * 1000;
                return { ok: true };
            } catch (error) {
                return { ok: false, message: describeAuthError(error) || AUTH_MESSAGES.generic };
            } finally {
                this.busy = false;
            }
        }

        /** Request a new code for the same email (Supabase replaces the old one). */
        resend() {
            if (!this.email) return Promise.resolve({ ok: false, message: AUTH_MESSAGES.noEmail });
            return this.request(this.email);
        }

        /** Check `code` with Supabase. On success supabase-js stores the
         *  session and fires SIGNED_IN, like a password login. */
        async verify(code) {
            if (this.busy) return { ok: false, busy: true };
            if (!this.email) return { ok: false, message: AUTH_MESSAGES.noEmail };
            const token = String(code || "").replace(/\s+/g, "");
            if (!CODE_RE.test(token)) return { ok: false, message: AUTH_MESSAGES.badCode };

            this.busy = true;
            try {
                const { data, error } = await this.auth.verifyOtp({ email: this.email, token, type: "email" });
                if (error) {
                    // Supabase reports wrong and expired codes the same way.
                    const message = error.code === "otp_expired" && this.expired
                        ? AUTH_MESSAGES.codeExpired
                        : describeAuthError(error);
                    return { ok: false, message, expired: this.expired };
                }
                if (!data?.session) return { ok: false, message: AUTH_MESSAGES.generic };
                return { ok: true, session: data.session };
            } catch (error) {
                return { ok: false, message: describeAuthError(error) || AUTH_MESSAGES.generic };
            } finally {
                this.busy = false;
            }
        }

        /** Start over with a different email. */
        reset() {
            this.email = "";
            this.sentAt = null;
            this.cooldownUntil = 0;
        }
    }

    function formatCountdown(seconds) {
        const s = Math.max(0, Math.floor(seconds));
        return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    }

    const api = { AUTH_MESSAGES, describeAuthError, OtpFlow, formatCountdown };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else Object.assign(root, api);
})(typeof window !== "undefined" ? window : globalThis);
