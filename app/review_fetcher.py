from google_play_scraper import search, reviews, Sort
from google_play_scraper import app as gp_app
from typing import Dict, List

KNOWN_APPS = {
    "doordash": "com.dd.doordash",
    "uber eats": "com.ubercab.eats",
    "instacart": "com.instacart.client",
    "airbnb": "com.airbnb.android",
    "lyft": "me.lyft.android",
    "grubhub": "com.grubhub.android",
    "ebay": "com.ebay.mobile",
    "etsy": "com.etsy.android",
    "amazon": "com.amazon.mShop.android.shopping",
}


def _format_app(sr: Dict) -> Dict:
    return {
        "app_id": sr.get("appId"),
        "title": sr.get("title", ""),
        "developer": sr.get("developer", ""),
        "icon": sr.get("icon", ""),
        "score": round(float(sr.get("score") or 0), 1),
        "installs": sr.get("installs", ""),
    }


def _valid_app(sr: Dict) -> bool:
    aid = sr.get("appId")
    return bool(aid and str(aid).lower() != "none")


def _try_direct_lookup(app_id: str) -> Dict | None:
    """Try to fetch app info directly by ID. Returns formatted app or None."""
    try:
        info = gp_app(app_id, lang="en", country="us")
        if info and info.get("title"):
            return {
                "app_id": app_id,
                "title": info.get("title", ""),
                "developer": info.get("developer", ""),
                "icon": info.get("icon", ""),
                "score": round(float(info.get("score") or 0), 1),
                "installs": info.get("installs", ""),
            }
    except Exception:
        pass
    return None


def _generate_candidate_ids(app_name: str) -> List[str]:
    """Generate likely app IDs from the search term."""
    clean = app_name.lower().strip()
    # Use first word for single-word names, full name for multi-word
    words = clean.split()
    first = words[0]
    no_dots = clean.replace(".", "").replace(" ", "")

    candidates = [
        f"com.{first}.{first}",
        f"com.{first}.android",
        f"com.{first}.app",
        f"com.{no_dots}.android",
        f"com.{no_dots}.app",
    ]

    # Check hardcoded dictionary (partial match)
    for key, app_id in KNOWN_APPS.items():
        if first in key or key in clean:
            candidates.append(app_id)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def search_google_play_apps(app_name: str) -> Dict:
    """Search Google Play and return matching + other apps with metadata."""
    first_word = app_name.strip().split()[0].lower()

    # Primary search
    search_results = search(app_name, n_hits=20, lang="en", country="us")
    if not search_results:
        search_results = []

    seen_ids = set()
    best = []
    other = []

    for sr in search_results:
        if not _valid_app(sr):
            continue
        aid = sr["appId"]
        if aid in seen_ids:
            continue
        seen_ids.add(aid)
        app = _format_app(sr)
        if first_word in app["title"].lower():
            best.append(app)
        else:
            other.append(app)

    # Direct lookup: try generated IDs and hardcoded fallbacks
    candidate_ids = _generate_candidate_ids(app_name)
    for cid in candidate_ids:
        if cid in seen_ids:
            continue
        result = _try_direct_lookup(cid)
        if result:
            seen_ids.add(cid)
            if first_word in result["title"].lower():
                best.insert(0, result)  # Put direct lookups first
            else:
                other.append(result)

    # Fallback search: if fewer than 3 best matches, run a second search
    if len(best) < 3:
        fallback_results = search(f"{app_name} app", n_hits=20, lang="en", country="us")
        for sr in (fallback_results or []):
            if not _valid_app(sr):
                continue
            aid = sr["appId"]
            if aid in seen_ids:
                continue
            seen_ids.add(aid)
            app = _format_app(sr)
            if first_word in app["title"].lower():
                best.append(app)
            else:
                other.append(app)

    # Cap results
    best = best[:5]
    other = other[:5]

    return {"best": best, "other": other}


def fetch_reviews_for_app(app_id: str, count: int = 50) -> Dict:
    """Fetch reviews for a specific app by its Google Play ID."""
    result, _ = reviews(
        app_id,
        lang="en",
        country="us",
        sort=Sort.NEWEST,
        count=count,
    )

    if not result:
        return {
            "error": "No reviews found. The app may be new or have limited reviews."
        }

    # Sort by full datetime descending (most recent first) before formatting
    from datetime import datetime
    epoch = datetime(2000, 1, 1)
    result.sort(key=lambda r: r["at"] if r.get("at") else epoch, reverse=True)

    formatted_reviews = []
    for r in result:
        formatted_reviews.append({
            "text": r["content"],
            "rating": r["score"],
            "date": r["at"].strftime("%Y-%m-%d") if r.get("at") else None,
            "thumbs_up": r.get("thumbsUpCount", 0),
        })

    return {
        "app_id": app_id,
        "review_count": len(formatted_reviews),
        "reviews": formatted_reviews,
    }
