// Unit tests for frontend/auth-flows.js (email code sign-in logic).
// Supabase Auth is mocked; no network, no real email.
//
//     node --test "tests/frontend/*.test.js"

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { OtpFlow, describeAuthError, AUTH_MESSAGES, formatCountdown } =
    require(path.join(__dirname, "..", "..", "frontend", "auth-flows.js"));

const EMAIL = "person@example.com";
const SESSION = { access_token: "a", refresh_token: "r", user: { id: "u1", email: EMAIL } };

/** Fake supabase.auth recording calls; responses are queued per method. */
function fakeAuth({ otp = [], verify = [] } = {}) {
    const calls = { signInWithOtp: [], verifyOtp: [] };
    const next = (queue, fallback) => (queue.length ? queue.shift() : fallback);
    return {
        calls,
        async signInWithOtp(args) {
            calls.signInWithOtp.push(args);
            const r = next(otp, { data: {}, error: null });
            if (r instanceof Error) throw r;
            return r;
        },
        async verifyOtp(args) {
            calls.verifyOtp.push(args);
            const r = next(verify, { data: { session: SESSION }, error: null });
            if (r instanceof Error) throw r;
            return r;
        },
    };
}

function clock(start = 1_000_000) {
    const c = { t: start, now: () => c.t, advance: (s) => { c.t += s * 1000; } };
    return c;
}

const otpExpired = { error: { status: 403, code: "otp_expired", message: "Token has expired or is invalid" }, data: {} };

test("request: sends a code to a valid email and creates new accounts", async () => {
    const auth = fakeAuth();
    const flow = new OtpFlow({ auth, redirectTo: "https://app.example/" });
    const result = await flow.request("  person@example.com ");
    assert.deepEqual(result, { ok: true });
    assert.equal(auth.calls.signInWithOtp.length, 1);
    assert.deepEqual(auth.calls.signInWithOtp[0], {
        email: EMAIL,
        options: { shouldCreateUser: true, emailRedirectTo: "https://app.example/" },
    });
    assert.equal(flow.email, EMAIL);
});

test("request: rejects an invalid email without calling Supabase", async () => {
    const auth = fakeAuth();
    const flow = new OtpFlow({ auth });
    for (const bad of ["", "not-an-email", "a@b", "two words@example.com"]) {
        const result = await flow.request(bad);
        assert.equal(result.ok, false);
        assert.equal(result.message, AUTH_MESSAGES.badEmail);
    }
    assert.equal(auth.calls.signInWithOtp.length, 0);
});

test("verify: a valid code signs in with type 'email' and returns the session", async () => {
    const auth = fakeAuth();
    const flow = new OtpFlow({ auth });
    await flow.request(EMAIL);
    const result = await flow.verify(" 123 456 ");
    assert.equal(result.ok, true);
    assert.equal(result.session, SESSION);
    assert.deepEqual(auth.calls.verifyOtp[0], { email: EMAIL, token: "123456", type: "email" });
});

test("verify: the email comes from the request step, not from the code form", async () => {
    const auth = fakeAuth();
    const flow = new OtpFlow({ auth });
    const result = await flow.verify("123456");
    assert.equal(result.ok, false);
    assert.equal(result.message, AUTH_MESSAGES.noEmail);
    assert.equal(auth.calls.verifyOtp.length, 0);
});

test("verify: an incorrect code is rejected by Supabase", async () => {
    const auth = fakeAuth({ verify: [otpExpired] });
    const flow = new OtpFlow({ auth });
    await flow.request(EMAIL);
    const result = await flow.verify("000000");
    assert.equal(result.ok, false);
    assert.equal(result.message, AUTH_MESSAGES.codeInvalid);
    assert.equal(result.expired, false);
});

test("verify: malformed codes are refused before reaching Supabase", async () => {
    const auth = fakeAuth();
    const flow = new OtpFlow({ auth });
    await flow.request(EMAIL);
    for (const bad of ["", "12345", "abcdef", "12345678901"]) {
        assert.equal((await flow.verify(bad)).message, AUTH_MESSAGES.badCode);
    }
    assert.equal(auth.calls.verifyOtp.length, 0);
});

test("verify: an expired code shows the expired message (Supabase still decides)", async () => {
    const c = clock();
    const auth = fakeAuth({ verify: [otpExpired] });
    const flow = new OtpFlow({ auth, now: c.now });
    await flow.request(EMAIL);
    c.advance(601);
    assert.equal(flow.expired, true);
    const result = await flow.verify("123456");
    assert.equal(auth.calls.verifyOtp.length, 1, "the browser never skips the server check");
    assert.equal(result.ok, false);
    assert.equal(result.expired, true);
    assert.equal(result.message, AUTH_MESSAGES.codeExpired);
});

test("verify: the browser countdown never accepts a code on its own", async () => {
    const auth = fakeAuth({ verify: [otpExpired] });
    const flow = new OtpFlow({ auth });
    await flow.request(EMAIL);
    assert.equal(flow.expired, false);
    assert.equal((await flow.verify("123456")).ok, false);
});

test("expiry countdown uses the configured 600 seconds", async () => {
    const c = clock();
    const flow = new OtpFlow({ auth: fakeAuth(), expirySeconds: 600, now: c.now });
    assert.equal(flow.secondsUntilExpiry, 0);
    await flow.request(EMAIL);
    assert.equal(flow.secondsUntilExpiry, 600);
    c.advance(599);
    assert.equal(flow.secondsUntilExpiry, 1);
    assert.equal(flow.expired, false);
    c.advance(1);
    assert.equal(flow.expired, true);
    assert.equal(formatCountdown(600), "10:00");
    assert.equal(formatCountdown(59), "0:59");
});

test("resend: waits for the cooldown, then requests a new code and restarts the expiry", async () => {
    const c = clock();
    const auth = fakeAuth();
    const flow = new OtpFlow({ auth, resendSeconds: 60, now: c.now });
    await flow.request(EMAIL);

    const early = await flow.resend();
    assert.equal(early.ok, false);
    assert.match(early.message, /wait 60 s/);
    assert.equal(auth.calls.signInWithOtp.length, 1);

    c.advance(300);
    assert.equal(flow.secondsUntilResend, 0);
    assert.deepEqual(await flow.resend(), { ok: true });
    assert.equal(auth.calls.signInWithOtp.length, 2);
    assert.equal(auth.calls.signInWithOtp[1].email, EMAIL);
    assert.equal(flow.secondsUntilExpiry, 600, "a new code gets a new expiry");
});

test("resend: the old code is not accepted if Supabase rejects it", async () => {
    const c = clock();
    // Supabase keeps only the newest code: the first verify (old code)
    // fails, the second (new code) succeeds.
    const auth = fakeAuth({ verify: [otpExpired, { data: { session: SESSION }, error: null }] });
    const flow = new OtpFlow({ auth, now: c.now });
    await flow.request(EMAIL);
    c.advance(61);
    await flow.resend();
    assert.equal((await flow.verify("111111")).ok, false);
    assert.equal((await flow.verify("222222")).ok, true);
});

test("rate limits: a 429 shows a wait message and starts the cooldown", async () => {
    const auth = fakeAuth({ otp: [{ data: {}, error: { status: 429, code: "over_email_send_rate_limit", message: "email rate limit exceeded" } }] });
    const flow = new OtpFlow({ auth });
    const result = await flow.request(EMAIL);
    assert.equal(result.ok, false);
    assert.equal(result.message, AUTH_MESSAGES.rateLimited);
    assert.ok(flow.secondsUntilResend > 0);
});

test("email delivery failure is reported as such", async () => {
    const auth = fakeAuth({ otp: [{ data: {}, error: { status: 500, code: "unexpected_failure", message: "Error sending magic link email" } }] });
    const result = await new OtpFlow({ auth }).request(EMAIL);
    assert.equal(result.message, AUTH_MESSAGES.sendFailed);
});

test("network errors are not reported as a wrong code", async () => {
    const netError = Object.assign(new Error("Failed to fetch"), { name: "AuthRetryableFetchError", status: 0 });
    const auth = fakeAuth({ verify: [{ data: {}, error: netError }, new TypeError("Failed to fetch")] });
    const flow = new OtpFlow({ auth });
    await flow.request(EMAIL);
    assert.equal((await flow.verify("123456")).message, AUTH_MESSAGES.network);
    assert.equal((await flow.verify("123456")).message, AUTH_MESSAGES.network);
    assert.equal(flow.busy, false, "never stuck loading after an error");
});

test("duplicate submissions are ignored while a request is in flight", async () => {
    let release;
    const auth = fakeAuth();
    auth.verifyOtp = (args) => {
        auth.calls.verifyOtp.push(args);
        return new Promise((resolve) => { release = () => resolve({ data: { session: SESSION }, error: null }); });
    };
    const flow = new OtpFlow({ auth });
    await flow.request(EMAIL);
    const first = flow.verify("123456");
    const second = await flow.verify("123456");
    assert.deepEqual(second, { ok: false, busy: true });
    assert.equal((await flow.resend()).busy, true);
    release();
    assert.equal((await first).ok, true);
    assert.equal(auth.calls.verifyOtp.length, 1);
    assert.equal(flow.busy, false);
});

test("a successful verify without a session is not treated as signed in", async () => {
    const auth = fakeAuth({ verify: [{ data: { session: null }, error: null }] });
    const flow = new OtpFlow({ auth });
    await flow.request(EMAIL);
    assert.equal((await flow.verify("123456")).ok, false);
});

test("reset: switching email clears the previous request", async () => {
    const flow = new OtpFlow({ auth: fakeAuth() });
    await flow.request(EMAIL);
    flow.reset();
    assert.equal(flow.email, "");
    assert.equal(flow.secondsUntilResend, 0);
    assert.equal((await flow.verify("123456")).message, AUTH_MESSAGES.noEmail);
});

test("describeAuthError keeps password, network and server errors apart", () => {
    assert.equal(describeAuthError(null), null);
    assert.equal(describeAuthError({ status: 400, code: "invalid_credentials", message: "Invalid login credentials" }), AUTH_MESSAGES.invalidCredentials);
    assert.equal(describeAuthError(new TypeError("Failed to fetch")), AUTH_MESSAGES.network);
    assert.equal(describeAuthError({ status: 503, message: "upstream" }), AUTH_MESSAGES.network);
    assert.equal(describeAuthError({ status: 400, code: "email_not_confirmed" }), AUTH_MESSAGES.emailNotConfirmed);
    assert.equal(describeAuthError({ status: 422, code: "signup_disabled" }), AUTH_MESSAGES.signupDisabled);
    assert.equal(describeAuthError({ status: 500, message: "internal: pq: relation \"x\" ..." }), AUTH_MESSAGES.generic);
    assert.equal(describeAuthError({ status: 422, code: "weak_password", message: "Password should be at least 6 characters." }), "Password should be at least 6 characters.");
    assert.equal(describeAuthError({ status: 400, message: "x".repeat(400) }), AUTH_MESSAGES.generic);
});

test("codes and emails are never written to the console", async () => {
    const seen = [];
    const original = { log: console.log, info: console.info, warn: console.warn, error: console.error, debug: console.debug };
    for (const k of Object.keys(original)) console[k] = (...args) => seen.push(args.join(" "));
    try {
        const auth = fakeAuth({ verify: [otpExpired, new TypeError("Failed to fetch")] });
        const flow = new OtpFlow({ auth });
        await flow.request(EMAIL);
        await flow.verify("987654");
        await flow.verify("987654");
    } finally {
        Object.assign(console, original);
    }
    assert.ok(!seen.some((line) => line.includes("987654") || line.includes(EMAIL)));
});
