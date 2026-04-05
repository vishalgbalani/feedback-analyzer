# Customer Feedback Analyzer — Architecture Spec

Build a production-ready Customer Feedback Analyzer that takes raw customer feedback (pasted text, CSV upload, or fetched from Google Play) and produces a structured analysis with theme categorization, sentiment scoring, product opportunities, and executive summary.

This is Build 5 in a PM toolkit series. Key architectural difference from previous builds: this agent processes USER-PROVIDED DATA or FETCHED APP REVIEWS, not web-searched intelligence. The LLM analyzes actual customer voices, not news articles.

## Architecture

Three-agent pipeline using the OpenAI Agents SDK. Analysis is performed on the user's input data or on reviews fetched from Google Play.

### Input Handling

The app must accept feedback in THREE formats, presented as tabs in the frontend:

**Tab 1: Paste Feedback**
User pastes raw feedback directly into a text area. Could be:
- App store reviews (copy-pasted)
- NPS comments
- Support ticket excerpts
- User interview quotes
- Survey responses
Each piece of feedback should be separated by a newline.

**Tab 2: Upload CSV**
User uploads a CSV file containing feedback. The app should:
- Accept .csv files up to 5MB
- Auto-detect the feedback column (look for columns named: "feedback", "comment", "review", "text", "description", "message", "response", "body", "content")
- If no matching column name found, use the column with the longest average text length
- Optionally detect a "rating" or "score" or "stars" column for correlation analysis
- Optionally detect a "date" or "created_at" or "timestamp" column for temporal analysis
- Show the user which columns were detected before running analysis
- Parse and validate the CSV, skip empty rows

**Tab 3: Fetch App Reviews**
User types an app name → backend uses the `google-play-scraper` Python package to fetch 150-200 real reviews from Google Play, including star ratings and dates. This makes the tool instantly usable without the user needing any data.

Implementation:
```python
from google_play_scraper import app, Sort, reviews

def fetch_google_play_reviews(app_name: str) -> dict:
    """
    Search for an app on Google Play and fetch recent reviews.
    Returns reviews with text, rating, and date.
    """
    # Step 1: Search for the app to get its ID
    from google_play_scraper import search
    search_results = search(app_name, n_hits=3, lang="en", country="us")
    
    if not search_results:
        return {"error": f"No apps found matching '{app_name}'"}
    
    app_id = search_results[0]["appId"]
    app_title = search_results[0]["title"]
    
    # Step 2: Fetch reviews
    result, _ = reviews(
        app_id,
        lang="en",
        country="us",
        sort=Sort.NEWEST,
        count=200
    )
    
    # Step 3: Format for analysis
    formatted_reviews = []
    for r in result:
        formatted_reviews.append({
            "text": r["content"],
            "rating": r["score"],
            "date": r["at"].strftime("%Y-%m-%d") if r.get("at") else None,
            "thumbs_up": r.get("thumbsUpCount", 0)
        })
    
    return {
        "app_name": app_title,
        "app_id": app_id,
        "review_count": len(formatted_reviews),
        "reviews": formatted_reviews
    }
```

The frontend for Tab 3 should:
- Have an input field with placeholder "e.g. Uber, DoorDash, Airbnb, Spotify"
- Show a "Fetch Reviews" button
- After fetching, display: "Found {app_title} — {count} reviews fetched. [Analyze]"
- Show a preview of 3 sample reviews before analysis
- Include a note: "Reviews fetched from Google Play Store"

### Chunking Strategy

Customer feedback can be large (200+ reviews). The LLM has token limits. Implement chunking:

1. Split feedback into chunks of ~50 reviews each
2. Run the Categorizer agent on each chunk separately
3. Aggregate results across chunks (merge theme counts, combine sentiment scores, deduplicate themes)
4. Pass aggregated results to the Analyst agent
5. The Writer produces the final report from the aggregated analysis

For deduplication: themes like "Pricing" and "Price" and "Cost" should be merged. The Analyst agent should consolidate similar themes in its instructions.

### Agents

1. **Feedback Categorizer** — takes a chunk of feedback and for each piece:
   - Assigns a primary theme (discovered organically from the data, NOT pre-defined)
   - Assigns sentiment: Positive, Negative, Neutral, Mixed
   - Extracts key phrases (the specific language users use)
   - Flags type: Feature Request, Bug Report, Praise, Complaint, Question
   - If a rating/score is available, notes it alongside the categorization

   The Categorizer should identify themes organically from the data. Common themes that might emerge include: Pricing/Fees, UX/Usability, Performance/Speed, Customer Support, Feature Requests, Onboarding, Reliability/Bugs, Content Quality, Value for Money — but the agent must discover these from the actual feedback, not use a hardcoded list.

2. **Insights Analyst** — takes the aggregated categorization results and produces:
   - Theme distribution (what % of feedback mentions each theme)
   - Sentiment breakdown (overall and per-theme)
   - Rating correlation (which themes correlate with 1-star vs 5-star reviews, if ratings available)
   - Top pain points ranked by frequency AND intensity of language
   - Product opportunities (gaps between what users want and what they have)
   - Feature requests ranked by frequency
   - Standout quotes (5 most impactful user quotes that capture key themes — use actual user words)
   - Contradictions (where different users want opposite things)
   - Theme consolidation (merge similar themes: "Pricing" + "Cost" + "Fees" → "Pricing & Fees")

3. **Report Writer** — produces a structured executive report using Pydantic output model. Writes for a PM audience — actionable, specific, data-backed. Should feel like a senior UX researcher's synthesis, not a generic AI summary. Must include actual user quotes as evidence.

## Pydantic Output Models

```python
class ThemeSummary(BaseModel):
    theme_name: str  # e.g. "Pricing Transparency", "Onboarding Friction"
    mention_count: int  # How many feedback items mention this theme
    percentage: float  # % of total feedback
    sentiment_breakdown: str  # e.g. "72% Negative, 18% Neutral, 10% Positive"
    avg_rating: Optional[float]  # Average star rating for this theme (if ratings available)
    feedback_types: str  # e.g. "60% Complaints, 25% Feature Requests, 15% Bug Reports"
    key_phrases: List[str]  # 3-5 actual phrases users used
    sample_quotes: List[str]  # 2-3 representative quotes from actual feedback

class FeedbackAnalysis(BaseModel):
    total_feedback_count: int  # Total pieces of feedback analyzed
    data_source: str  # "Pasted text", "CSV upload", or "Google Play: {app_name}"
    overall_sentiment: str  # e.g. "Predominantly Negative (62%) with strong praise around core functionality"
    avg_rating: Optional[float]  # Overall average rating if available
    rating_distribution: Optional[str]  # e.g. "1★: 25%, 2★: 15%, 3★: 20%, 4★: 22%, 5★: 18%"
    theme_summaries: List[ThemeSummary]  # Ordered by mention_count descending
    top_pain_points: List[str]  # 5-7 ranked pain points with evidence
    product_opportunities: List[str]  # 5-7 actionable opportunities derived from feedback gaps
    feature_requests: List[str]  # 3-5 most requested features with frequency indicators
    standout_quotes: List[str]  # 5 most impactful quotes capturing the voice of the user
    contradictions: List[str]  # 2-3 areas where users disagree or want opposite things
    executive_summary: str  # 3-5 paragraph summary suitable for a leadership review
    recommendations: List[str]  # 5-7 specific PM recommendations based on the analysis
    data_quality_notes: str  # Notes on input data: size, any issues, confidence level
    source_comparison: Optional[str]  # 2-3 paragraphs comparing themes across data sources (only when combining Google Play + user data)

## Production Setup

1. `app/main.py` — FastAPI server with:
   - CORS enabled
   - GET /health endpoint
   - POST /analyze endpoint — accepts JSON with `feedback_text` (string) for pasted text
   - POST /analyze-csv endpoint — accepts multipart file upload for CSV
   - POST /fetch-reviews endpoint — accepts JSON with `app_name` (string), fetches Google Play reviews, returns review data for preview
   - POST /analyze-reviews endpoint — accepts JSON with `app_name` (string), fetches and analyzes in one step with SSE streaming
   - SSE streaming for all analysis endpoints
   - IP-based rate limiting: 5 requests per IP per day with friendly 429 message: {"error": "Daily limit reached (5 analyses per day). This is a free demo — thanks for trying it!"}
   - POST /analyze-combined endpoint — accepts JSON with google_play_reviews (list) and additional_feedback (string or file), merges datasets, runs combined analysis with SSE streaming
   - Max file size: 5MB

2. `app/pipeline.py` — Three-agent pipeline with chunking:
   - Accept feedback as list of dicts (with optional rating and date fields)
   - Split into chunks of ~50 items
   - Run Categorizer on each chunk
   - Aggregate and deduplicate themes
   - Run Analyst on aggregated data
   - Run Writer for final report
   - Yield SSE events at each stage:
     - "Parsing feedback... ({count} items detected)"
     - "Analyzing chunk {n} of {total}..."
     - "Categorization complete. {theme_count} themes identified across {count} reviews."
     - "Synthesizing insights and identifying patterns..."
     - "Generating executive report..."

3. `app/models.py` — All Pydantic models

4. `app/csv_parser.py` — CSV parsing utility:
   - Read CSV with pandas
   - Auto-detect feedback column (match column names, fallback to longest text column)
   - Auto-detect optional rating and date columns
   - Return list of dicts with text, rating (optional), date (optional)
   - Handle encoding issues gracefully (try utf-8, fallback to latin-1, then cp1252)
   - Return column detection results for frontend preview

5. `app/review_fetcher.py` — Google Play review fetcher:
   - Search for app by name
   - Fetch 150-200 reviews with ratings and dates
   - Return structured data for preview and analysis
   - Handle app-not-found gracefully

6. `index.html` — Professional frontend with:
   - Title: "Customer Feedback Analyzer"
   - Subtitle: "Turn raw feedback into actionable product insights — built for Product Managers."
   - THREE input tabs with clean tab toggle UI:
     - Tab 1: "Paste Feedback" — large text area, placeholder: "Paste app store reviews, NPS comments, support tickets, or survey responses. One piece of feedback per line."
     - Tab 2: "Upload CSV" — file upload dropzone with drag-and-drop support, instructions: "Upload a CSV with a feedback column. We'll auto-detect the right columns." Show detected columns before analysis.
     - Tab 3: "Fetch App Reviews" — input field with placeholder "e.g. Uber, DoorDash, Airbnb, Spotify", "Fetch Reviews" button, shows preview (app name, review count, 3 sample reviews) before "Analyze" button.
   - "Analyze Feedback" button (changes label per tab: "Analyze Text" / "Analyze CSV" / "Fetch & Analyze")
   - Streaming progress indicators with item counts and chunk progress
   - Formatted output with:
     - Executive Summary (prominent, at top, in a highlighted card)
     - Overall Sentiment with visual indicator (colored bar: green for positive, red for negative, yellow for mixed)
     - Rating Distribution if available (horizontal bar chart or text breakdown)
     - Theme Breakdown — each theme as an expandable card showing: name, count badge, percentage, sentiment color bar, average rating (if available), feedback types, key phrases as tags, sample quotes in italic
     - Themes should be sortable: "Sort by: Count | Sentiment | Rating"
     - Top Pain Points section (red-accented)
     - Product Opportunities section (green-accented)
     - Feature Requests section
     - Standout Quotes section (large text, quotation styling, visually distinct)
     - Contradictions section (amber-accented)
     - Recommendations section
     - Data Quality Notes (subtle, collapsed by default)
   - "Try with sample data" button that pre-loads 20 sample reviews for instant demo
   - Footer: "Powered by GPT-4o-mini"

7. `test_api.py` — Test script with 20 sample e-commerce app reviews
8. `Procfile`, `railway.toml`, `requirements.txt` (include google-play-scraper, pandas)

## Combined Analysis Workflow

After Stage 1 (themed review browser from Google Play) is displayed, show a "Combine & Analyze" panel between Stage 1 and Stage 2:

### "Combine & Analyze" Panel
- Title: "Want a deeper picture? Add your own feedback."
- Subtitle: "Combine app store reviews with your internal data — support tickets, NPS comments, survey responses — for a complete analysis."
- Two options side by side:
  - "Paste Additional Feedback" — text area for pasting
  - "Upload CSV" — file upload dropzone
- A summary bar showing: "Google Play reviews: {count} | Your feedback: {count} | Total: {combined_count}"
- "Analyze Combined Feedback" button
- Option to skip: "Or analyze Google Play reviews only →" link that proceeds with just the fetched reviews
NOTE: The "Combine & Analyze" panel ONLY appears when the user started with Tab 3 (Fetch App Reviews). For Tab 1 (Paste) and Tab 2 (Upload CSV), skip the combine step and go directly from Stage 1 to Stage 2.

### How it works in the backend:
- The POST /analyze-combined endpoint accepts:
  - `google_play_reviews`: list of reviews already fetched (passed from Stage 1)
  - `additional_feedback`: string (pasted text) OR file upload (CSV)
- The pipeline merges both datasets, tags each review with its source ("Google Play" or "User Provided")
- The Categorizer processes the combined dataset
- The Analyst notes patterns across sources: "App store users focus on UX issues while support tickets emphasize billing problems — suggesting different user segments surface different pain points."
- The final output includes a "Source Breakdown" showing how themes differ between public reviews and internal feedback

### Stage 2 output additions:
- Add a `source_comparison` field to FeedbackAnalysis:
```python
  source_comparison: Optional[str]  # 2-3 paragraphs comparing themes across data sources
```
- The executive summary should reference both sources when combined
- Theme cards should show a small source badge: "GP: 34 | Internal: 12" per theme

User types "Uber" 
  → Fetches 200 Google Play reviews
  → Stage 1: Themed review browser appears
  → "Want deeper insights? Add your support tickets"
  → User uploads CSV of internal feedback
  → Stage 2: Combined analysis with source comparison

## Two-Stage Output Display

The frontend must display results in TWO distinct stages, visually separated:

### Stage 1: "What Your Users Are Saying" (Thematic Review Browser)
After the Categorizer agent finishes (before the Analyst runs), display an intermediate output:
- Section title: "What Your Users Are Saying"
- Show each discovered theme as a collapsible card
- Each card header shows: Theme name, review count badge, average rating (if available)
- Expanding a card reveals the actual reviews categorized under that theme, each showing: star rating (★), review text, date
- Cards are sorted by review count (most mentioned first)
- This section streams in as soon as the Categorizer finishes, BEFORE the full analysis

The SSE events should yield this intermediate output:
- Event type: "categorization" with the themed/grouped review data
- The frontend renders Stage 1 immediately when this event arrives

### Stage 2: "What It Means" (Strategic Analysis)
After the Analyst and Writer finish, display the full analysis below Stage 1:
- Section title: "What It Means"
- All the existing output sections: Executive Summary, Sentiment, Theme Summaries, Pain Points, Opportunities, Recommendations, etc.
- This section streams in after Stage 1 is already visible

The user experience flow:
1. User submits query → progress indicators stream
2. "Categorizing reviews..." → Stage 1 appears (themed review browser)
3. "Analyzing patterns..." → Stage 2 appears below (strategic analysis)
4. User can browse Stage 1 while Stage 2 is still generating


## Sample Test Data (embed in test_api.py AND in the "Try with sample data" frontend button)

20 sample reviews for a fictional e-commerce app "ShopFast":

1. "Love the fast shipping! Got my order in 2 days. Best experience ever." ★5
2. "The search is terrible. I can never find what I'm looking for." ★2
3. "Why did the prices go up? Same item was $10 cheaper last month." ★1
4. "Customer support took 3 days to respond. Unacceptable." ★1
5. "Great variety of products. Found exactly what I needed." ★4
6. "App crashes every time I try to filter by price. So frustrating." ★1
7. "Free shipping over $50 is a game changer. Love this feature!" ★5
8. "The checkout process has too many steps. Just let me pay already." ★2
9. "Received a damaged item. Return process was surprisingly easy though." ★3
10. "Would love a wishlist feature so I can save items for later." ★3
11. "Prices are way too high compared to Amazon. Not competitive." ★2
12. "The app is beautiful and easy to navigate. Best shopping app I've used." ★5
13. "Notifications are out of control. I get 5 push notifications a day." ★2
14. "Delivery tracking is excellent. Love knowing exactly where my package is." ★5
15. "I can't believe there's no Apple Pay option in 2026. Come on." ★2
16. "The recommendation engine is spot on. It knows what I want before I do." ★4
17. "Refund took 2 weeks to process. That's way too long." ★1
18. "Just discovered the price match feature. This app keeps getting better." ★5
19. "Search results are full of irrelevant items. Needs major improvement." ★2
20. "Solid app overall but the font size is too small for me. Need accessibility options." ★3

## Key Details

- Use `from agents import Agent, Runner, function_tool` (NOT openai_agents)
- Use gpt-4o-mini as the model (cost efficiency)
- ONLY needs OPENAI_API_KEY — no other API keys required
- google-play-scraper requires NO API key — it scrapes Google Play directly
- Unique session ID per request via uuid
- SSE streams real-time status with progress counts
- The Categorizer MUST discover themes organically from the data
- Chunking is critical — test with the full 200-review Google Play fetch
- Theme deduplication in the Analyst must merge similar themes (e.g. "Price" + "Pricing" + "Cost")
- Frontend theme cards should show actual user quotes — this is the "proof" that makes the analysis credible
- The "Try with sample data" button should work without any backend call for the sample data — just pre-populate the text area, then the user clicks Analyze

## Error Handling

- If CSV parsing fails: return clear error ("Could not parse CSV. Ensure comma-separated with header row.")
- If no feedback column detected: list available columns, ask user to specify
- If feedback < 3 items: suggest minimum 10 for meaningful analysis
- If feedback > 2000 items: truncate to most recent 2000 with a note
- If Google Play app not found: return "No apps found matching '{name}'. Try the exact app name."
- If Google Play fetch returns 0 reviews: return "No reviews found. The app may be new or have limited reviews."
- If any chunk fails: skip that chunk, note the gap, continue with remaining
- Empty or whitespace-only feedback items: silently filter out
- Non-English reviews from Google Play: include them but note "some reviews may be in other languages" in data_quality_notes

## Cost Notes

- No external API costs — only OpenAI (gpt-4o-mini)
- google-play-scraper is free, no API key needed
- ~50 reviews per chunk × ~500 tokens per review = ~25,000 tokens per chunk
- gpt-4o-mini: ~$0.01-0.03 per analysis of 200 reviews
- Rate limiting at 5/IP/day is generous since costs are minimal
- Most expensive operation: analyzing 200 Google Play reviews ≈ $0.03

Deliver all files ready to run locally and deploy to Railway.
