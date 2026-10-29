// Unit tests for frontend/math.js (finding LaTeX formulas in answers).
//
//     node --test tests/frontend/math.test.js

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { extractMath } = require(path.join(__dirname, "..", "..", "frontend", "math.js"));

test("inline and display formulas become placeholders", () => {
    const { text, math } = extractMath("A die shows 2 with \\( \\frac{1}{6} \\).\n\\[\n\\frac{1}{6} \\times \\frac{1}{6} = \\frac{1}{36}\n\\]\nSo (D).");
    assert.equal(text, "A die shows 2 with @@MATH0@@.\n@@MATH1@@\nSo (D).");
    assert.deepEqual(math, [
        { tex: "\\frac{1}{6}", display: false },
        { tex: "\\frac{1}{6} \\times \\frac{1}{6} = \\frac{1}{36}", display: true },
    ]);
});

test("$$ ... $$ is display math; a single $ is just text", () => {
    const { text, math } = extractMath("Raised $30 M and $5 M. $$x^2$$");
    assert.equal(text, "Raised $30 M and $5 M. @@MATH0@@");
    assert.deepEqual(math, [{ tex: "x^2", display: true }]);
});

test("formulas inside code are left alone", () => {
    const source = "Use `\\(x\\)` in code.\n```latex\n\\[ a^2 \\]\n```\nBut \\(y\\) is math.";
    const { text, math } = extractMath(source);
    assert.ok(text.includes("`\\(x\\)`"));
    assert.ok(text.includes("\\[ a^2 \\]"));
    assert.deepEqual(math, [{ tex: "y", display: false }]);
});

test("an unclosed formula (still streaming) stays as text", () => {
    const { text, math } = extractMath("Next: \\[ \\frac{1}{");
    assert.equal(text, "Next: \\[ \\frac{1}{");
    assert.equal(math.length, 0);
});

test("pipes and asterisks inside a formula can't break tables or bold", () => {
    const { text, math } = extractMath("| a | \\(|x| * 2\\) |");
    assert.equal(text, "| a | @@MATH0@@ |");
    assert.equal(math[0].tex, "|x| * 2");
});

test("empty formulas and empty input are ignored", () => {
    assert.deepEqual(extractMath("\\(  \\)"), { text: "\\(  \\)", math: [] });
    assert.deepEqual(extractMath(null), { text: "", math: [] });
});
