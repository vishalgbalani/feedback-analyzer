from pydantic import BaseModel
from typing import List, Optional


class ThemeSummary(BaseModel):
    theme_name: str
    mention_count: int
    percentage: float
    sentiment_breakdown: str
    avg_rating: Optional[float] = None
    feedback_types: str
    key_phrases: List[str]
    sample_quotes: List[str]


class FeedbackAnalysis(BaseModel):
    total_feedback_count: int
    data_source: str
    overall_sentiment: str
    avg_rating: Optional[float] = None
    rating_distribution: Optional[str] = None
    theme_summaries: List[ThemeSummary]
    top_pain_points: List[str]
    product_opportunities: List[str]
    feature_requests: List[str]
    standout_quotes: List[str]
    contradictions: List[str]
    executive_summary: str
    recommendations: List[str]
    data_quality_notes: str
    source_comparison: Optional[str] = None


class CategorizedItem(BaseModel):
    i: int
    t: str  # theme
    s: str  # sentiment: Positive, Negative, Neutral


class CategorizedChunk(BaseModel):
    items: List[CategorizedItem]


class ColumnDetection(BaseModel):
    feedback_column: str
    rating_column: Optional[str] = None
    date_column: Optional[str] = None
    row_count: int
    sample_values: List[str]
