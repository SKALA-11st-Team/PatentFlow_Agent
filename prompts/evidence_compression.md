You are compressing patent valuation evidence.

Return ONLY one JSON object. Do not include markdown, explanations, or code fences.

Goal:
- Keep only valuation-relevant facts.
- Do not invent facts.
- Write Korean summaries and facts.
- Use concise factual sentences.

Allowed related_axes values:
- legal
- technology
- market
- economic
- business_fit
- strategy

For source_type "news", return this shape:
{
  "is_relevant": true,
  "related_axes": ["market"],
  "relation_type": "direct",
  "compressed_summary": "핵심 요약",
  "key_facts": ["사실 1", "사실 2"],
  "axis_context": {
    "market": "해당 평가축과 연결되는 맥락"
  }
}

For source_type "industry_report", return this shape:
{
  "related_axes": ["market"],
  "compressed_summary": "핵심 요약",
  "key_facts": ["사실 1", "사실 2"]
}

If the evidence is not useful for patent valuation, set "is_relevant": false for news.
