"""Test script with 20 sample e-commerce app reviews for ShopFast."""

import requests
import json
import sys

BASE_URL = "http://localhost:8000"

SAMPLE_REVIEWS = [
    "Love the fast shipping! Got my order in 2 days. Best experience ever. ★5",
    "The search is terrible. I can never find what I'm looking for. ★2",
    "Why did the prices go up? Same item was $10 cheaper last month. ★1",
    "Customer support took 3 days to respond. Unacceptable. ★1",
    "Great variety of products. Found exactly what I needed. ★4",
    "App crashes every time I try to filter by price. So frustrating. ★1",
    "Free shipping over $50 is a game changer. Love this feature! ★5",
    "The checkout process has too many steps. Just let me pay already. ★2",
    "Received a damaged item. Return process was surprisingly easy though. ★3",
    "Would love a wishlist feature so I can save items for later. ★3",
    "Prices are way too high compared to Amazon. Not competitive. ★2",
    "The app is beautiful and easy to navigate. Best shopping app I've used. ★5",
    "Notifications are out of control. I get 5 push notifications a day. ★2",
    "Delivery tracking is excellent. Love knowing exactly where my package is. ★5",
    "I can't believe there's no Apple Pay option in 2026. Come on. ★2",
    "The recommendation engine is spot on. It knows what I want before I do. ★4",
    "Refund took 2 weeks to process. That's way too long. ★1",
    "Just discovered the price match feature. This app keeps getting better. ★5",
    "Search results are full of irrelevant items. Needs major improvement. ★2",
    "Solid app overall but the font size is too small for me. Need accessibility options. ★3",
]


def test_health():
    r = requests.get(f"{BASE_URL}/health")
    print(f"Health: {r.status_code} — {r.json()}")


def test_analyze():
    print("\n--- Testing /analyze (pasted text) ---")
    feedback_text = "\n".join(SAMPLE_REVIEWS)
    r = requests.post(
        f"{BASE_URL}/analyze",
        json={"feedback_text": feedback_text},
        stream=True,
    )
    print(f"Status: {r.status_code}")
    for line in r.iter_lines(decode_unicode=True):
        if line:
            print(line)


def test_fetch_reviews():
    print("\n--- Testing /fetch-reviews ---")
    r = requests.post(
        f"{BASE_URL}/fetch-reviews",
        json={"app_name": "Uber"},
    )
    print(f"Status: {r.status_code}")
    data = r.json()
    if "error" not in data:
        print(f"App: {data['app_name']}, Reviews: {data['review_count']}")
        for rev in data["reviews"][:3]:
            print(f"  [{rev['rating']}★] {rev['text'][:80]}...")
    else:
        print(data)


if __name__ == "__main__":
    test_health()
    if "--fetch" in sys.argv:
        test_fetch_reviews()
    else:
        test_analyze()
