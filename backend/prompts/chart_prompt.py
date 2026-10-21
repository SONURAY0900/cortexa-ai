"""
Instructions that let the answer include a chart. The frontend draws any
```chart block (JSON) as a bar or line chart under the text; see
frontend/charts.js for the accepted format and limits.

CHART_RULES is plain text; CHART_RULES_TEMPLATE has its braces escaped so
it can be placed inside a prompt that is filled with str.format().
"""

CHART_RULES = """
Charts (optional):
- Add a chart only if the user asks for one, or the answer compares a number
  across 3+ items, or shows a number changing over time.
- Use only numbers stated in the provided information; with no provided
  information, only well-established figures. Never invent or estimate
  numbers for a chart; if they are not available, add no chart.
- After the explanation, add at most ONE fenced code block with the language
  "chart" containing only JSON, for example:
```chart
{"type": "line", "title": "Example title", "unit": "million users", "labels": ["2021", "2022", "2023"], "series": [{"name": "Users", "data": [12.5, 18, 26.4]}]}
```
- "type": "bar" to compare items, "line" for change over time (labels in
  time order). At most 20 labels and 4 series; one plain number per label
  in "data" (no units or commas; null if missing). "unit" must name the
  scale of those numbers (e.g. "million people" for 1428 meaning
  1,428 million).
- Still state the key numbers in the text.
"""

CHART_RULES_TEMPLATE = CHART_RULES.replace("{", "{{").replace("}", "}}")
