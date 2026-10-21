// Unit tests for frontend/charts.js (chart spec validation and axis ticks).
// The spec comes from the model, so anything malformed must be rejected
// or clamped rather than drawn.
//
//     node --test tests/frontend/charts.test.js

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { parseChartSpec, niceTicks, formatValue, LIMITS } =
    require(path.join(__dirname, "..", "..", "frontend", "charts.js"));

const valid = {
    type: "bar",
    title: "Population (2023)",
    unit: "million",
    labels: ["India", "China", "USA"],
    series: [{ name: "Population", data: [1428, 1425, 340] }],
};

test("a valid spec is accepted from JSON text or an object", () => {
    for (const input of [JSON.stringify(valid), valid]) {
        const spec = parseChartSpec(input);
        assert.equal(spec.type, "bar");
        assert.deepEqual(spec.labels, ["India", "China", "USA"]);
        assert.deepEqual(spec.series[0].data, [1428, 1425, 340]);
        assert.equal(spec.unit, "million");
    }
});

test("malformed input is rejected", () => {
    for (const bad of [
        "not json", "[1,2,3]", "null", "{}",
        { labels: [], series: [{ data: [] }] },
        { labels: ["a"], series: [] },
        { labels: ["a", "b"], series: [{ name: "x", data: ["n/a", null] }] },
        { labels: ["a"], series: "x" },
    ]) {
        assert.equal(parseChartSpec(bad), null, JSON.stringify(bad));
    }
});

test("unknown types become bar; line is kept", () => {
    assert.equal(parseChartSpec({ ...valid, type: "pie" }).type, "bar");
    assert.equal(parseChartSpec({ ...valid, type: "line" }).type, "line");
});

test("numbers are cleaned and data is aligned to the labels", () => {
    const spec = parseChartSpec({ labels: ["a", "b", "c"], series: [{ name: "s", data: ["1,200", "x", 3, 4] }] });
    assert.deepEqual(spec.series[0].data, [1200, null, 3]);
    const short = parseChartSpec({ labels: ["a", "b", "c"], series: [{ name: "s", data: [5] }] });
    assert.deepEqual(short.series[0].data, [5, null, null]);
});

test("labels, series and text are capped", () => {
    const many = Array.from({ length: 100 }, (_, i) => `L${i}`);
    const spec = parseChartSpec({
        title: "t".repeat(500),
        labels: many,
        series: Array.from({ length: 9 }, (_, i) => ({ name: "n".repeat(100), data: many.map(() => i) })),
    });
    assert.equal(spec.labels.length, LIMITS.barLabels);
    assert.equal(spec.series.length, LIMITS.series);
    assert.ok(spec.title.length <= LIMITS.title);
    assert.ok(spec.series[0].name.length <= LIMITS.text);
    assert.equal(parseChartSpec({ type: "line", labels: many, series: [{ name: "s", data: many.map(() => 1) }] }).labels.length, LIMITS.lineLabels);
});

test("missing series names get a placeholder", () => {
    assert.equal(parseChartSpec({ labels: ["a"], series: [{ data: [1] }] }).series[0].name, "Series 1");
});

test("ticks are round, include zero and cover the data", () => {
    const ticks = niceTicks(0, 1428);
    assert.equal(ticks[0], 0);
    assert.ok(ticks.at(-1) >= 1428);
    const step = ticks[1] - ticks[0];
    assert.ok([1, 2, 2.5, 5].some((m) => (step / 10 ** Math.floor(Math.log10(step))) === m));
    const negative = niceTicks(-35, 80);
    assert.ok(negative[0] <= -35 && negative.includes(0) && negative.at(-1) >= 80);
    assert.deepEqual(niceTicks(0, 0).slice(0, 1), [0]);
});

test("values are formatted compactly from 10,000", () => {
    assert.equal(formatValue(1428), "1,428");
    assert.equal(formatValue(1.256), "1.26");
    assert.equal(formatValue(1425000), "1.4M");
    assert.equal(formatValue(null), "—");
});
