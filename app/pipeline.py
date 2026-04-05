import json
import asyncio
from typing import List, Dict, Optional, AsyncGenerator
from agents import Agent, Runner

from app.models import CategorizedChunk, FeedbackAnalysis

CATEGORIZER_INSTRUCTIONS = """Categorize each review. Return JSON: {"items":[{"i":0,"t":"Theme","s":"Positive"},...]}.
i = review index (0-based), t = theme name, s = Positive/Negative/Neutral.
Use 6-8 broad themes max. Short names: "Pricing", "App Performance", "Search", "Shipping", "Customer Support", "UI/UX", etc. Never create overlapping themes."""

ANALYST_WRITER_INSTRUCTIONS = """You are a senior product insights analyst AND UX researcher writing for a Product Manager audience. You receive categorized customer feedback data grouped by theme, with review text, ratings, and sentiment labels.

Your job in ONE step:
1. Consolidate similar themes (merge "Pricing"+"Cost"+"Fees" etc.)
2. For each theme: extract key phrases, identify feedback types, pick 2-3 sample quotes
3. Calculate sentiment breakdown per theme and overall
4. Identify top pain points, product opportunities, feature requests
5. Pick 5 standout quotes (exact user words)
6. Find contradictions where users want opposite things
7. Write an executive summary (3-5 paragraphs) for leadership
8. Provide 5-7 specific PM recommendations

If data comes from multiple sources, compare themes across sources.

Style: actionable, data-backed, like a senior UX researcher — not a generic AI summary. Use actual user quotes as evidence.

Return JSON matching this EXACT structure:
{
  "total_feedback_count": int,
  "data_source": str,
  "overall_sentiment": str,
  "avg_rating": float|null,
  "rating_distribution": str|null (e.g. "1★: 25%, 2★: 15%, 3★: 20%, 4★: 22%, 5★: 18%"),
  "theme_summaries": [{"theme_name": str, "mention_count": int, "percentage": float, "sentiment_breakdown": str, "avg_rating": float|null, "feedback_types": str, "key_phrases": [str], "sample_quotes": [str]}],
  "top_pain_points": [str] (5-7),
  "product_opportunities": [str] (5-7),
  "feature_requests": [str] (3-5),
  "standout_quotes": [str] (5, exact user words),
  "contradictions": [str] (2-3),
  "executive_summary": str (3-5 paragraphs),
  "recommendations": [str] (5-7),
  "data_quality_notes": str,
  "source_comparison": str|null
}
Order theme_summaries by mention_count desc."""

DEDUP_INSTRUCTIONS = """Group these theme names by topic. Return JSON: {"Canonical Name": ["original1", "original2"], ...}. Merge similar themes. Keep distinct ones separate. Target 6-10 final themes. Every original must appear in exactly one group."""


def _chunk_feedback(items: List[Dict], chunk_size: int = 100) -> List[List[Dict]]:
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


def _compute_stats(items: List[Dict]) -> Dict:
    total = len(items)
    ratings = [item["rating"] for item in items if item.get("rating")]
    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else None
    dates = [item["date"] for item in items if item.get("date")]
    date_range = None
    if dates:
        sorted_dates = sorted(dates)
        try:
            from datetime import datetime
            first = datetime.strptime(sorted_dates[0][:10], "%Y-%m-%d").strftime("%b %Y")
            last = datetime.strptime(sorted_dates[-1][:10], "%Y-%m-%d").strftime("%b %Y")
            date_range = f"{first} - {last}" if first != last else first
        except Exception:
            pass
    return {"total": total, "avg_rating": avg_rating, "date_range": date_range, "has_ratings": len(ratings) > 0}


def _compress_reviews(items: List[Dict]) -> List[Dict]:
    compressed = []
    for item in items:
        c = dict(item)
        text = " ".join(c.get("text", "").split())
        if len(text) > 200:
            text = text[:200].rsplit(" ", 1)[0] + "..."
        c["text"] = text
        compressed.append(c)
    return compressed


async def _deduplicate_themes(themes: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
    theme_summary = {name: len(reviews) for name, reviews in themes.items()}
    dedup_agent = Agent(name="Theme Deduplicator", instructions=DEDUP_INSTRUCTIONS, model="gpt-4o-mini")
    prompt = f"Group these {len(theme_summary)} themes:\n{json.dumps(theme_summary)}"
    try:
        result = await Runner.run(dedup_agent, prompt)
        output = result.final_output
        if isinstance(output, str):
            cleaned = output.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
            mapping = json.loads(cleaned)
        else:
            mapping = output

        merged: Dict[str, List[Dict]] = {}
        mapped_originals = set()
        for canonical_name, original_names in mapping.items():
            merged[canonical_name] = []
            for orig in original_names:
                if orig in themes:
                    merged[canonical_name].extend(themes[orig])
                    mapped_originals.add(orig)
        for orig_name, revs in themes.items():
            if orig_name not in mapped_originals:
                merged[orig_name] = revs
        for canonical_name, revs in merged.items():
            for review in revs:
                review["theme"] = canonical_name
        return merged
    except Exception:
        return themes


async def run_analysis(
    feedback_items: List[Dict],
    data_source: str = "Pasted text",
    source_labels: Optional[Dict[int, str]] = None,
) -> AsyncGenerator[Dict, None]:
    """Run the two-agent pipeline with SSE events."""

    # Filter empty items
    feedback_items = [item for item in feedback_items if item.get("text", "").strip()]

    if not feedback_items:
        yield {"event": "error", "data": "No valid feedback items found."}
        return

    truncated = False
    if len(feedback_items) > 2000:
        feedback_items = feedback_items[:2000]
        truncated = True

    total_count = len(feedback_items)
    yield {
        "event": "status",
        "data": f"Parsing feedback... ({total_count} items detected)"
        + (" (truncated to 2000)" if truncated else ""),
    }

    if total_count < 3:
        yield {
            "event": "error",
            "data": f"Please provide at least 10 feedback items for meaningful analysis. Only found {total_count}.",
        }
        return

    # Emit instant stats
    stats = _compute_stats(feedback_items)
    yield {"event": "stats", "data": json.dumps(stats)}

    # Add source labels
    if source_labels:
        for i, item in enumerate(feedback_items):
            if i in source_labels:
                item["source"] = source_labels[i]

    # Compress for LLM
    compressed_items = _compress_reviews(feedback_items)

    # Chunk
    chunks = _chunk_feedback(compressed_items)
    num_chunks = len(chunks)

    # Stage 1: Fast categorization — minimal output (index, theme, sentiment)
    categorizer = Agent(
        name="Categorizer",
        instructions=CATEGORIZER_INSTRUCTIONS,
        model="gpt-4o-mini",
        output_type=CategorizedChunk,
    )

    async def _categorize_chunk(chunk: List[Dict], chunk_offset: int):
        """Send only review texts to categorizer, get back theme+sentiment per index."""
        # Build minimal input: just index and text
        minimal = [{"i": chunk_offset + idx, "text": r["text"]} for idx, r in enumerate(chunk)]
        prompt = f"Categorize {len(minimal)} reviews:\n{json.dumps(minimal)}"
        try:
            result = await Runner.run(categorizer, prompt)
            output = result.final_output
            if isinstance(output, CategorizedChunk):
                return [item.model_dump() for item in output.items]
            raw = json.loads(output) if isinstance(output, str) else output
            if isinstance(raw, dict) and "items" in raw:
                return raw["items"]
            elif isinstance(raw, list):
                return raw
            return []
        except Exception:
            return []

    yield {"event": "progress", "data": json.dumps({"stage": "categorizing", "pct": 10, "chunks_total": num_chunks, "chunks_done": 0})}

    all_labels = []  # list of {"i": idx, "t": theme, "s": sentiment}
    chunk_offset = 0

    if num_chunks == 1:
        labels = await _categorize_chunk(chunks[0], 0)
        all_labels.extend(labels)
        # Build review dicts for incremental display
        chunk_reviews = _merge_labels_with_reviews(labels, feedback_items)
        yield {"event": "chunk_result", "data": json.dumps({"reviews": chunk_reviews, "chunk_idx": 0, "chunks_total": 1}, default=str)}
        yield {"event": "progress", "data": json.dumps({"stage": "categorizing", "pct": 50, "chunks_total": 1, "chunks_done": 1})}
    else:
        offsets = []
        off = 0
        for chunk in chunks:
            offsets.append(off)
            off += len(chunk)
        tasks = [asyncio.create_task(_categorize_chunk(chunk, offsets[idx])) for idx, chunk in enumerate(chunks)]
        chunks_done = 0
        for coro in asyncio.as_completed(tasks):
            labels = await coro
            chunks_done += 1
            all_labels.extend(labels)
            chunk_reviews = _merge_labels_with_reviews(labels, feedback_items)
            pct = 10 + int(40 * chunks_done / num_chunks)
            yield {"event": "chunk_result", "data": json.dumps({"reviews": chunk_reviews, "chunk_idx": chunks_done - 1, "chunks_total": num_chunks}, default=str)}
            yield {"event": "progress", "data": json.dumps({"stage": "categorizing", "pct": pct, "chunks_total": num_chunks, "chunks_done": chunks_done})}

    if not all_labels:
        yield {"event": "error", "data": "Categorization failed for all chunks."}
        return

    # Merge labels with original reviews to build themed groups
    all_reviews = _merge_labels_with_reviews(all_labels, feedback_items)

    # Group by theme
    themes: Dict[str, List[Dict]] = {}
    for review in all_reviews:
        theme = review.get("theme", "Uncategorized")
        if theme not in themes:
            themes[theme] = []
        themes[theme].append(review)

    # Deduplicate overlapping themes
    if len(themes) > 8:
        yield {"event": "progress", "data": json.dumps({"stage": "consolidating", "pct": 52})}
        themes = await _deduplicate_themes(themes)

    yield {"event": "progress", "data": json.dumps({"stage": "categorized", "pct": 55})}

    # Yield final Stage 1 categorization data
    yield {"event": "categorization", "data": json.dumps({
        "themes": {
            name: revs for name, revs in sorted(
                themes.items(), key=lambda x: len(x[1]), reverse=True
            )
        },
        "total_count": total_count,
    }, default=str)}

    # Stage 2: Analyst + Writer (single LLM call)
    yield {"event": "progress", "data": json.dumps({"stage": "analyzing", "pct": 60})}

    analyst_writer = Agent(
        name="Analyst & Report Writer",
        instructions=ANALYST_WRITER_INSTRUCTIONS,
        model="gpt-4o-mini",
        output_type=FeedbackAnalysis,
    )

    has_multiple_sources = source_labels and len(set(source_labels.values())) > 1

    # Build a compact representation for the analyst: theme → reviews with text + rating + sentiment
    theme_data = {}
    for name, revs in themes.items():
        theme_data[name] = {
            "count": len(revs),
            "reviews": [{"text": r["text"], "rating": r.get("rating"), "sentiment": r.get("sentiment", ""), "source": r.get("source")} for r in revs[:15]],
        }

    prompt = f"""Analyze this categorized feedback and produce the final executive report.

Data source: {data_source}
Total: {total_count} reviews
{"MULTIPLE SOURCES — include source_comparison." if has_multiple_sources else ""}

Reviews by theme:
{json.dumps(theme_data, default=str)}

Theme counts: {json.dumps({n: len(r) for n, r in themes.items()})}

Generate FeedbackAnalysis JSON."""

    try:
        result = await Runner.run(analyst_writer, prompt)
        final_output = result.final_output
        if isinstance(final_output, FeedbackAnalysis):
            analysis_dict = final_output.model_dump()
        else:
            analysis_dict = json.loads(final_output) if isinstance(final_output, str) else final_output
    except Exception as e:
        yield {"event": "error", "data": f"Report generation failed: {str(e)[:200]}"}
        return

    yield {"event": "progress", "data": json.dumps({"stage": "done", "pct": 100})}
    yield {"event": "analysis", "data": json.dumps(analysis_dict, default=str)}
    yield {"event": "done", "data": "Analysis complete."}


def _merge_labels_with_reviews(labels: List[Dict], original_items: List[Dict]) -> List[Dict]:
    """Merge categorizer labels (i, t, s) back with original review data."""
    reviews = []
    for label in labels:
        idx = label.get("i", -1)
        if 0 <= idx < len(original_items):
            orig = original_items[idx]
            reviews.append({
                "text": orig.get("text", ""),
                "rating": orig.get("rating"),
                "date": orig.get("date"),
                "source": orig.get("source"),
                "theme": label.get("t", "Uncategorized"),
                "sentiment": label.get("s", "Neutral"),
            })
        else:
            # Index out of range — use label data only
            reviews.append({
                "text": "",
                "theme": label.get("t", "Uncategorized"),
                "sentiment": label.get("s", "Neutral"),
            })
    return reviews
