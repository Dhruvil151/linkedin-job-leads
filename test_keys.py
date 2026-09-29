#!/usr/bin/env python3
"""Verify every key in GEMINI_API_KEYS works via direct HTTP (same method as scorer)."""

import requests
import config

body = {
    "contents": [{"role": "user", "parts": [{"text": "Reply with just the word: OK"}]}],
    "generationConfig": {"temperature": 0},
}

print(f"\nTesting {len(config.GEMINI_API_KEYS)} Gemini API keys...\n")
print(f"{'#':<3} {'Model':<26} Status")
print("-" * 78)

working = []
for i, (key, model) in enumerate(config.GEMINI_API_KEYS, 1):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=45)
        if r.status_code == 200:
            print(f"{i:<3} {model:<26} OK")
            working.append(i)
        else:
            print(f"{i:<3} {model:<26} FAIL {r.status_code}")
    except Exception as exc:
        print(f"{i:<3} {model:<26} ERROR ({type(exc).__name__})")

print("-" * 78)
print(f"\n{len(working)}/{len(config.GEMINI_API_KEYS)} keys working: {working}\n")
