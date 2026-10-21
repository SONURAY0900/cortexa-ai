/* =====================================================
   Cortexa AI — charts (bar and line), no library.

   Used by chat answers (```chart blocks written by the model) and the
   admin dashboard. Every chart has a hover/focus tooltip, a legend for
   two or more series and a table view, so no value depends on colour.

   Accepted spec (JSON):
     { "type": "bar" | "line", "title": "...", "unit": "...",
       "labels": ["A", "B", ...],
       "series": [{ "name": "...", "data": [number | null, ...] }] }
   The spec is untrusted (it comes from the model): parseChartSpec()
   validates and clamps it, and all text is inserted with textContent.
   ===================================================== */

(function (root) {
    "use strict";

    const LIMITS = { barLabels: 20, lineLabels: 60, series: 4, title: 120, unit: 24, text: 40 };

    function cleanText(value, max) {
        const text = String(value ?? "").replace(/\s+/g, " ").trim();
        return text.length > max ? text.slice(0, max - 1) + "…" : text;
    }

    function toNumber(value) {
        if (value === null || value === undefined || value === "") return null;
        const n = typeof value === "number" ? value : Number(String(value).replace(/[,\s]/g, ""));
        return Number.isFinite(n) ? n : null;
    }

    /** Validated spec, or null if the input can't be drawn. */
    function parseChartSpec(input) {
        let raw = input;
        if (typeof input === "string") {
            try { raw = JSON.parse(input); } catch (_) { return null; }
        }
        if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;

        const type = raw.type === "line" ? "line" : "bar";
        const maxLabels = type === "line" ? LIMITS.lineLabels : LIMITS.barLabels;
        if (!Array.isArray(raw.labels) || !raw.labels.length) return null;
        const labels = raw.labels.slice(0, maxLabels).map((l) => cleanText(l, LIMITS.text));

        if (!Array.isArray(raw.series) || !raw.series.length) return null;
        const series = raw.series.slice(0, LIMITS.series)
            .filter((s) => s && typeof s === "object" && Array.isArray(s.data))
            .map((s, i) => ({
                name: cleanText(s.name, LIMITS.text) || `Series ${i + 1}`,
                data: labels.map((_, j) => toNumber(s.data[j])),
            }));
        if (!series.length || !series.some((s) => s.data.some((v) => v !== null))) return null;

        return {
            type,
            title: cleanText(raw.title, LIMITS.title),
            unit: cleanText(raw.unit, LIMITS.unit),
            labels,
            series,
        };
    }

    function niceStep(range, count) {
        const rough = range / Math.max(1, count);
        const power = Math.pow(10, Math.floor(Math.log10(rough)));
        const fraction = rough / power;
        return (fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10) * power;
    }

    /** Round axis ticks covering [min, max]; the scale always includes 0. */
    function niceTicks(min, max, count = 5) {
        let lo = Math.min(0, min);
        let hi = Math.max(0, max);
        if (lo === hi) hi = lo + 1;
        const step = niceStep(hi - lo, count);
        lo = Math.floor(lo / step) * step;
        hi = Math.ceil(hi / step) * step;
        const ticks = [];
        for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Number(v.toPrecision(12)));
        return ticks;
    }

    const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
    const plain = new Intl.NumberFormat("en", { maximumFractionDigits: 2 });

    function formatValue(value) {
        if (value === null || value === undefined) return "—";
        return Math.abs(value) >= 10000 ? compact.format(value) : plain.format(value);
    }

    const api = { parseChartSpec, niceTicks, formatValue, LIMITS };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
        return;
    }

    /* ---------------- DOM rendering (browser only) ---------------- */

    const SVG_NS = "http://www.w3.org/2000/svg";
    const FONT = '12px Inter, system-ui, -apple-system, "Segoe UI", Arial, sans-serif';
    let measureCtx = null;

    function textWidth(text) {
        measureCtx ||= document.createElement("canvas").getContext("2d");
        measureCtx.font = FONT;
        return measureCtx.measureText(text).width;
    }

    function fitText(text, width) {
        if (textWidth(text) <= width) return text;
        let cut = text;
        while (cut.length > 1 && textWidth(cut + "…") > width) cut = cut.slice(0, -1);
        return cut + "…";
    }

    function svg(tag, attrs = {}, children = []) {
        const node = document.createElementNS(SVG_NS, tag);
        for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) node.setAttribute(k, v);
        for (const child of children) if (child) node.append(child);
        return node;
    }

    function svgText(text, attrs) {
        const node = svg("text", attrs);
        node.textContent = text;
        return node;
    }

    function html(tag, cls, text) {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    const color = (i) => `var(--series-${i + 1})`;

    /** Bar path with a 4px rounded data end and a square baseline end. */
    function barPath(x0, x1, y, h) {
        const r = Math.min(4, Math.abs(x1 - x0) / 2, h / 2);
        if (x1 >= x0) {
            return `M${x0},${y}H${x1 - r}Q${x1},${y} ${x1},${y + r}V${y + h - r}Q${x1},${y + h} ${x1 - r},${y + h}H${x0}Z`;
        }
        return `M${x0},${y}H${x1 + r}Q${x1},${y} ${x1},${y + r}V${y + h - r}Q${x1},${y + h} ${x1 + r},${y + h}H${x0}Z`;
    }

    function tooltipRows(tip, title, rows) {
        tip.replaceChildren(html("div", "chart-tip-title", title));
        for (const row of rows) {
            const line = html("div", "chart-tip-row");
            const key = html("span", "chart-tip-key");
            key.style.background = row.color;
            line.append(html("strong", null, row.value), html("span", "chart-tip-name", row.name), key);
            tip.append(line);
        }
    }

    function placeTip(plot, tip, x, y) {
        tip.hidden = false;
        const pw = plot.clientWidth;
        const tw = tip.offsetWidth;
        const th = tip.offsetHeight;
        let left = x + 14;
        if (left + tw > pw) left = Math.max(0, x - tw - 14);
        tip.style.left = `${left}px`;
        tip.style.top = `${Math.max(0, y - th / 2)}px`;
    }

    function drawBar(spec, plot, tip, width) {
        const multi = spec.series.length > 1;
        const thick = multi ? 12 : 20;
        const groupH = spec.series.length * thick + (spec.series.length - 1) * 2;
        const rowH = groupH + (multi ? 16 : 14);
        const labelW = Math.min(width * 0.38, Math.max(...spec.labels.map(textWidth)) + 8, 180);
        const valueW = multi ? 8 : 56;
        const top = 6, axisH = 22;
        const plotW = Math.max(60, width - labelW - valueW - 8);
        const height = top + spec.labels.length * rowH + axisH;

        const values = spec.series.flatMap((s) => s.data).filter((v) => v !== null);
        const ticks = niceTicks(Math.min(...values), Math.max(...values), Math.max(2, Math.floor(plotW / 90)));
        const lo = ticks[0], hi = ticks[ticks.length - 1];
        const x = (v) => labelW + ((v - lo) / (hi - lo)) * plotW;
        const zero = x(0);

        const root = svg("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": spec.title || "Bar chart" });
        const grid = svg("g", { class: "chart-grid" });
        for (const t of ticks) {
            grid.append(svg("line", { x1: x(t), x2: x(t), y1: top, y2: height - axisH, class: t === 0 ? "chart-baseline" : "chart-gridline" }));
            grid.append(svgText(formatValue(t), { x: x(t), y: height - 6, class: "chart-axis", "text-anchor": "middle" }));
        }
        root.append(grid);

        spec.labels.forEach((label, i) => {
            const rowTop = top + i * rowH + (rowH - groupH) / 2;
            root.append(svgText(fitText(label, labelW - 8), { x: labelW - 8, y: rowTop + groupH / 2 + 4, class: "chart-label", "text-anchor": "end" }));
            spec.series.forEach((s, k) => {
                const v = s.data[i];
                if (v === null) return;
                const y = rowTop + k * (thick + 2);
                const bar = svg("path", { d: barPath(zero, x(v), y, thick), class: "chart-mark", tabindex: "0", style: `fill:${color(k)}` });
                const show = (px, py) => {
                    tooltipRows(tip, label, [{ value: formatValue(v) + (spec.unit ? ` ${spec.unit}` : ""), name: s.name, color: color(k) }]);
                    placeTip(plot, tip, px, py);
                };
                bar.addEventListener("pointermove", (e) => {
                    const box = plot.getBoundingClientRect();
                    show(e.clientX - box.left, e.clientY - box.top);
                });
                bar.addEventListener("focus", () => show(x(v), y + thick / 2));
                bar.addEventListener("pointerleave", () => { tip.hidden = true; });
                bar.addEventListener("blur", () => { tip.hidden = true; });
                root.append(bar);
                if (!multi) {
                    const end = x(v);
                    root.append(svgText(formatValue(v), {
                        x: v >= 0 ? end + 6 : end - 6, y: y + thick / 2 + 4, class: "chart-value",
                        "text-anchor": v >= 0 ? "start" : "end",
                    }));
                }
            });
        });
        return root;
    }

    function drawLine(spec, plot, tip, width) {
        const values = spec.series.flatMap((s) => s.data).filter((v) => v !== null);
        const ticks = niceTicks(Math.min(...values), Math.max(...values), 4);
        const axisW = Math.max(...ticks.map((t) => textWidth(formatValue(t)))) + 10;
        const single = spec.series.length === 1;
        const lastLabel = single ? textWidth(formatValue(spec.series[0].data.filter((v) => v !== null).at(-1))) + 14 : 10;
        const left = axisW, right = Math.max(12, lastLabel), top = 10, bottom = 26;
        const height = 240;
        const plotW = Math.max(60, width - left - right);
        const plotH = height - top - bottom;
        const lo = ticks[0], hi = ticks[ticks.length - 1];
        const n = spec.labels.length;
        const x = (i) => left + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
        const y = (v) => top + plotH - ((v - lo) / (hi - lo)) * plotH;

        const root = svg("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": spec.title || "Line chart" });
        const grid = svg("g", { class: "chart-grid" });
        for (const t of ticks) {
            grid.append(svg("line", { x1: left, x2: left + plotW, y1: y(t), y2: y(t), class: t === 0 ? "chart-baseline" : "chart-gridline" }));
            grid.append(svgText(formatValue(t), { x: left - 8, y: y(t) + 4, class: "chart-axis", "text-anchor": "end" }));
        }
        const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(plotW / 64))));
        // First and last label always; others every `every` steps, skipping
        // one that would crowd the last label.
        const labelled = (i) => i === 0 || i === n - 1 || (i % every === 0 && n - 1 - i >= every);
        spec.labels.forEach((label, i) => {
            if (!labelled(i)) return;
            grid.append(svgText(fitText(label, 64 * every - 8), {
                x: x(i), y: height - 8, class: "chart-axis",
                "text-anchor": n > 1 && i === 0 ? "start" : n > 1 && i === n - 1 ? "end" : "middle",
            }));
        });
        root.append(grid);

        spec.series.forEach((s, k) => {
            let d = "";
            let pen = false;
            s.data.forEach((v, i) => {
                if (v === null) { pen = false; return; }
                d += `${pen ? "L" : "M"}${x(i)},${y(v)}`;
                pen = true;
            });
            if (single && d) {
                const first = s.data.findIndex((v) => v !== null);
                const last = s.data.length - 1 - [...s.data].reverse().findIndex((v) => v !== null);
                if (last > first) {
                    root.append(svg("path", {
                        d: `${d}L${x(last)},${y(Math.max(lo, 0))}L${x(first)},${y(Math.max(lo, 0))}Z`,
                        class: "chart-area", style: `fill:${color(k)}`,
                    }));
                }
            }
            root.append(svg("path", { d, class: "chart-line", style: `stroke:${color(k)}` }));
            const lastIndex = s.data.length - 1 - [...s.data].reverse().findIndex((v) => v !== null);
            if (lastIndex < s.data.length && s.data[lastIndex] !== null) {
                root.append(svg("circle", { cx: x(lastIndex), cy: y(s.data[lastIndex]), r: 4, class: "chart-dot", style: `fill:${color(k)}` }));
                if (single) {
                    root.append(svgText(formatValue(s.data[lastIndex]), { x: x(lastIndex) + 8, y: y(s.data[lastIndex]) + 4, class: "chart-value" }));
                }
            }
        });

        // Crosshair: snaps to the nearest label; one tooltip lists every series.
        const cross = svg("line", { y1: top, y2: top + plotH, class: "chart-cross", visibility: "hidden" });
        const marks = spec.series.map((s, k) => svg("circle", { r: 4, class: "chart-dot", style: `fill:${color(k)}`, visibility: "hidden" }));
        const hit = svg("rect", { x: left, y: top, width: plotW, height: plotH, class: "chart-hit", tabindex: "0" });
        root.append(cross, ...marks, hit);
        let current = n - 1;

        const show = (i) => {
            current = i;
            cross.setAttribute("x1", x(i));
            cross.setAttribute("x2", x(i));
            cross.setAttribute("visibility", "visible");
            spec.series.forEach((s, k) => {
                const v = s.data[i];
                marks[k].setAttribute("visibility", v === null ? "hidden" : "visible");
                if (v !== null) { marks[k].setAttribute("cx", x(i)); marks[k].setAttribute("cy", y(v)); }
            });
            tooltipRows(tip, spec.labels[i], spec.series.map((s, k) => ({
                value: formatValue(s.data[i]) + (s.data[i] !== null && spec.unit ? ` ${spec.unit}` : ""),
                name: s.name, color: color(k),
            })));
            placeTip(plot, tip, x(i), top + plotH / 3);
        };
        const hide = () => {
            tip.hidden = true;
            cross.setAttribute("visibility", "hidden");
            marks.forEach((m) => m.setAttribute("visibility", "hidden"));
        };
        hit.addEventListener("pointermove", (e) => {
            const box = root.getBoundingClientRect();
            const px = e.clientX - box.left;
            show(Math.max(0, Math.min(n - 1, Math.round(n === 1 ? 0 : ((px - left) / plotW) * (n - 1)))));
        });
        hit.addEventListener("pointerleave", hide);
        hit.addEventListener("focus", () => show(current));
        hit.addEventListener("blur", hide);
        hit.addEventListener("keydown", (e) => {
            if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
                e.preventDefault();
                show(Math.max(0, Math.min(n - 1, current + (e.key === "ArrowRight" ? 1 : -1))));
            }
        });
        return root;
    }

    function dataTable(spec) {
        const table = html("table");
        const head = html("tr");
        head.append(html("th", null, ""), ...spec.series.map((s) => html("th", null, s.name + (spec.unit ? ` (${spec.unit})` : ""))));
        const body = html("tbody");
        spec.labels.forEach((label, i) => {
            const row = html("tr");
            row.append(html("td", null, label), ...spec.series.map((s) => html("td", "num", formatValue(s.data[i]))));
            body.append(row);
        });
        const thead = html("thead");
        thead.append(head);
        table.append(thead, body);
        const wrap = html("div", "table-wrap");
        wrap.append(table);
        return wrap;
    }

    /** A <figure> with the chart, legend, tooltip and table view.
     *  options.hideTitle: the surrounding panel already names the chart. */
    function createChart(spec, options = {}) {
        const figure = html("figure", "chart");
        const head = html("figcaption", "chart-head");
        const titles = html("div", "chart-titles");
        if (!options.hideTitle) {
            titles.append(html("span", "chart-title", spec.title || (spec.type === "line" ? "Trend" : "Comparison")));
            if (spec.unit) titles.append(html("span", "chart-unit", spec.unit));
        }
        const toggle = html("button", "chart-toggle", "Table");
        toggle.type = "button";
        toggle.setAttribute("aria-pressed", "false");
        head.append(titles, toggle);
        figure.append(head);

        if (spec.series.length > 1) {
            const legend = html("div", "chart-legend");
            spec.series.forEach((s, k) => {
                const item = html("span", "chart-legend-item");
                const key = html("span", spec.type === "line" ? "chart-key line" : "chart-key");
                key.style.background = color(k);
                item.append(key, document.createTextNode(s.name));
                legend.append(item);
            });
            figure.append(legend);
        }

        const plot = html("div", "chart-plot");
        const tip = html("div", "chart-tip");
        tip.hidden = true;
        tip.setAttribute("role", "status");
        const table = dataTable(spec);
        table.hidden = true;
        figure.append(plot, table);

        let drawnWidth = 0;
        const draw = () => {
            const width = Math.floor(plot.clientWidth || 600);
            if (width === drawnWidth || width < 120) return;
            drawnWidth = width;
            const chart = spec.type === "line" ? drawLine(spec, plot, tip, width) : drawBar(spec, plot, tip, width);
            plot.replaceChildren(chart, tip);
        };

        toggle.addEventListener("click", () => {
            const showTable = table.hidden;
            table.hidden = !showTable;
            plot.hidden = showTable;
            toggle.textContent = showTable ? "Chart" : "Table";
            toggle.setAttribute("aria-pressed", String(showTable));
            if (!showTable) { drawnWidth = 0; draw(); }
        });

        if ("ResizeObserver" in window) new ResizeObserver(() => { if (!plot.hidden) draw(); }).observe(plot);
        requestAnimationFrame(draw);
        return figure;
    }

    Object.assign(root, { Charts: { ...api, createChart } });
})(typeof window !== "undefined" ? window : globalThis);
