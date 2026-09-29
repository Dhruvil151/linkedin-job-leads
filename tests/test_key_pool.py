import os
import unittest
from unittest.mock import patch

import config
from gemini_scorer import _KeyRotator, _call_gemini


class KeyPoolTests(unittest.TestCase):
    def test_ordered_pairs(self):
        with patch.dict(os.environ, {"GEMINI_API_KEYS_JSON": '[["a","model-a"],["b","model-b"]]'}, clear=True):
            self.assertEqual(config.gemini_entries(), [("a", "model-a"), ("b", "model-b")])

    def test_missing_pool(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(config.gemini_entries(), [])

    def test_invalid_pool(self):
        for raw in ('not-json', '[]', '[["secret"]]', '[["a",""]]'):
            with patch.dict(os.environ, {"GEMINI_API_KEYS_JSON": raw}, clear=True):
                with self.assertRaises(ValueError) as error:
                    config.gemini_entries()
                self.assertNotIn("secret", str(error.exception))

    def test_quota_switch_preserves_model(self):
        with patch.object(config, "GEMINI_API_KEYS", [("a", "model-a"), ("b", "model-b")]):
            rotator = _KeyRotator()
        calls = []
        def call(body):
            calls.append((rotator.current_key, rotator.current_model))
            if rotator.current_key == "a":
                return 429, {"error": {"code": 429, "message": "quota exhausted"}}
            return 200, {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]}
        with patch.object(rotator, "call", side_effect=call):
            self.assertEqual(_call_gemini(rotator, "resume", []), [])
        self.assertEqual(calls, [("a", "model-a"), ("b", "model-b")])

    def test_all_exhausted_stops(self):
        with patch.object(config, "GEMINI_API_KEYS", [("a", "model-a"), ("b", "model-b")]):
            rotator = _KeyRotator()
        with patch.object(rotator, "call", return_value=(429, {"error": {"code": 429}})) as call:
            self.assertEqual(_call_gemini(rotator, "resume", []), [])
            self.assertEqual(call.call_count, 2)

    def test_repeated_temporary_limit_switches(self):
        with patch.object(config, "GEMINI_API_KEYS", [("a", "model-a"), ("b", "model-b")]):
            rotator = _KeyRotator()
        with patch.object(rotator, "call", return_value=(429, {"error": {"code": 429, "message": "retry in 1s"}})) as call, patch("gemini_scorer.time.sleep"):
            self.assertEqual(_call_gemini(rotator, "resume", []), [])
            self.assertEqual(call.call_count, 6)
