/* =====================================================
   Cortexa AI — math in answers.

   The model writes LaTeX: \( ... \) inline, \[ ... \] or $$ ... $$ on
   its own line. renderMarkdown() swaps each formula for a placeholder
   before the markdown pass (which would mangle "\", "*" and "|"), then
   puts back a <span class="math" data-tex="..."> showing the raw TeX.
   renderIn() draws those spans with KaTeX, loaded from cdnjs (with SRI)
   the first time an answer contains math. Single $ is not treated as
   math, so prices like "$30 M" stay text.
   ===================================================== */

(function (root) {
    "use strict";

    // Code (fenced or inline) is matched first and left alone.
    const PATTERN = /(```[\s\S]*?(?:```|$)|`[^`\n]*`)|\\\[([\s\S]+?)\\\]|\$\$([\s\S]+?)\$\$|\\\(([\s\S]+?)\\\)/g;

    /** { text with @@MATHn@@ placeholders, math: [{ tex, display }] } */
    function extractMath(source) {
        const math = [];
        const text = String(source ?? "").replace(PATTERN, (match, code, block, dollars, inline) => {
            if (code) return match;
            const tex = (block ?? dollars ?? inline).trim();
            if (!tex) return match;
            math.push({ tex, display: inline === undefined });
            return `@@MATH${math.length - 1}@@`;
        });
        return { text, math };
    }

    const api = { extractMath };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
        return;
    }

    /* ---------------- KaTeX (browser only) ---------------- */

    const KATEX = "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.11/";
    const KATEX_JS_SRI = "sha384-7zkQWkzuo3B5mTepMUcHkMB5jZaolc2xDwL6VFqjFALcbeS9Ggm/Yr2r3Dy4lfFg";
    const KATEX_CSS_SRI = "sha384-nB0miv6/jRmo5UMMR1wu3Gz6NLsoTkbqJghGIsx//Rlm+ZU03BU6SQNC66uf4l5+";
    let ready = null;

    function attach(tag, attrs) {
        return new Promise((resolve, reject) => {
            const node = document.createElement(tag);
            Object.assign(node, attrs, { crossOrigin: "anonymous", onload: resolve, onerror: reject });
            document.head.append(node);
        });
    }

    function load() {
        ready ||= Promise.all([
            attach("link", { rel: "stylesheet", href: KATEX + "katex.min.css", integrity: KATEX_CSS_SRI }),
            attach("script", { src: KATEX + "katex.min.js", integrity: KATEX_JS_SRI }),
        ]).catch((error) => {
            ready = null;           // try again next time; raw TeX stays readable meanwhile
            throw error;
        });
        return ready;
    }

    /** Draw every pending formula inside `container`. */
    function renderIn(container) {
        const nodes = container.querySelectorAll(".math[data-tex]");
        if (!nodes.length) return;
        if (!root.katex) {
            load().then(() => renderIn(container), () => {});
            return;
        }
        nodes.forEach((node) => {
            const display = node.classList.contains("math-display");
            try {
                // Inline math in \displaystyle: fractions at full size, not
                // shrunk into the line. trust: false blocks \href, \url and
                // raw HTML; limits stop oversized or endlessly expanding macros.
                root.katex.render(display ? node.dataset.tex : "\\displaystyle " + node.dataset.tex, node, {
                    displayMode: display,
                    throwOnError: false, trust: false, strict: "ignore", maxSize: 20, maxExpand: 500,
                });
                node.removeAttribute("data-tex");
            } catch (_) { /* keep the raw TeX */ }
        });
    }

    Object.assign(root, { MathRender: { ...api, load, renderIn } });
})(typeof window !== "undefined" ? window : globalThis);
