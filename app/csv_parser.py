import io
import pandas as pd
from typing import List, Dict, Optional, Tuple

FEEDBACK_COLUMNS = [
    "feedback", "comment", "review", "text", "description",
    "message", "response", "body", "content",
]

RATING_COLUMNS = ["rating", "score", "stars"]
DATE_COLUMNS = ["date", "created_at", "timestamp"]


def _match_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    lower_map = {c.lower().strip(): c for c in df.columns}
    for candidate in candidates:
        if candidate in lower_map:
            return lower_map[candidate]
    return None


def _longest_text_column(df: pd.DataFrame) -> Optional[str]:
    best_col = None
    best_avg = 0
    for col in df.columns:
        if df[col].dtype == object:
            avg_len = df[col].dropna().astype(str).str.len().mean()
            if avg_len > best_avg:
                best_avg = avg_len
                best_col = col
    return best_col


def detect_columns(df: pd.DataFrame) -> Dict:
    feedback_col = _match_column(df, FEEDBACK_COLUMNS)
    if not feedback_col:
        feedback_col = _longest_text_column(df)

    rating_col = _match_column(df, RATING_COLUMNS)
    date_col = _match_column(df, DATE_COLUMNS)

    if not feedback_col:
        return {
            "error": "Could not detect a feedback column.",
            "available_columns": list(df.columns),
        }

    samples = df[feedback_col].dropna().astype(str).head(3).tolist()

    return {
        "feedback_column": feedback_col,
        "rating_column": rating_col,
        "date_column": date_col,
        "row_count": len(df),
        "sample_values": samples,
    }


def parse_csv(file_bytes: bytes) -> Tuple[List[Dict], Dict]:
    """Parse CSV bytes, return (feedback_items, column_info)."""
    df = None
    for encoding in ["utf-8", "latin-1", "cp1252"]:
        try:
            df = pd.read_csv(io.BytesIO(file_bytes), encoding=encoding)
            break
        except Exception:
            continue

    if df is None or df.empty:
        raise ValueError("Could not parse CSV. Ensure comma-separated with header row.")

    col_info = detect_columns(df)
    if "error" in col_info:
        raise ValueError(
            f"{col_info['error']} Available columns: {col_info['available_columns']}"
        )

    feedback_col = col_info["feedback_column"]
    rating_col = col_info.get("rating_column")
    date_col = col_info.get("date_column")

    items: List[Dict] = []
    for _, row in df.iterrows():
        text = str(row[feedback_col]).strip() if pd.notna(row[feedback_col]) else ""
        if not text:
            continue

        item = {"text": text}

        if rating_col and pd.notna(row.get(rating_col)):
            try:
                item["rating"] = int(float(row[rating_col]))
            except (ValueError, TypeError):
                pass

        if date_col and pd.notna(row.get(date_col)):
            item["date"] = str(row[date_col])

        items.append(item)

    return items, col_info
