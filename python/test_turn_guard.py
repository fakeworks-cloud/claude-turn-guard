#!/usr/bin/env python3
"""Offline tests for turn_guard.py.  Run:  python3 test_turn_guard.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import turn_guard  # noqa: E402

# Shapes taken from public reports (anthropics/claude-code issues) plus one synthetic example.
CASES = [
    ("#66267", "実装しました。\n\nuserすばらしい ただ、年度は１月始まりです", "label_fused"),
    ("#85215", "TFTP はポート 69/UDP です。\n\nusergitに commit", "label_fused_ascii"),
    ("#82619", "完成しました。\n\nuser完璧です。今回の作成の流れをスキル化することはできますか？", "label_fused"),
    ("#57928", "Monitoring continues.\n\nHuman: Kill it. First adjust the threshold", "label_colon"),
    ("#76378", "Nice work.\n\nusername hurt earlier today because of you", "label_fused_ascii"),
    ("label only", "The dryer stops.\n\nuser", "label_alone"),
    ("synthetic", "どういたしまして。\n\nuser今日はここまで。ログをまとめて保存しておいて", "label_fused"),
    ("label+space (Opus 5, live test)", "確認します。\nuser 試験用の行です", "label_fused"),
    ("label+space, English", "Done.\n\nuser let's stop here for today", "label_fused_ascii"),
    ("template", "OK.<|im_start|>user\nlet's stop here", "template_token"),
]
LEGIT = [
    "```python\nuser = get_user(1)\nusername = user.name\n```",
    "> user: this is a quoted example\n\nas shown above.",
    "DB の userテーブルを引く。行頭でなければ見ない。",
    "The user said it twice. Users can also configure this.",
    "- user_id を渡す\n- username は任意",
    "「user という文字列は怖いな」",
]


class T(unittest.TestCase):
    def test_catches_reported_shapes(self):
        for name, t, rule in CASES:
            with self.subTest(name):
                hit = turn_guard.scan_text(t)
                self.assertIsNotNone(hit)
                self.assertEqual(hit[0], rule)

    def test_legit_passes(self):
        for t in LEGIT:
            with self.subTest(t[:15]):
                self.assertIsNone(turn_guard.scan_text(t))

    def test_known_false_positive(self):
        # A line that legitimately starts with "userテーブル" is cut. Accepted trade-off.
        self.assertIsNotNone(turn_guard.scan_text("userテーブルを引く"))

    def test_inspect_drops_all_tool_calls_and_keeps_real_answer(self):
        v = turn_guard.inspect([{"type": "text", "text": "69/UDP です。\n\nusergitに commit"},
                                {"type": "tool_use", "id": "a", "name": "Bash", "input": {}},
                                {"type": "tool_use", "id": "b", "name": "Write", "input": {}}])
        self.assertFalse(v.ok)
        self.assertEqual(v.blocks, [{"type": "text", "text": "69/UDP です。"}])
        self.assertEqual([c["id"] for c in v.dropped_tool_calls], ["a", "b"])

    def test_clean_reply_untouched(self):
        blocks = [{"type": "text", "text": "hi"}, {"type": "tool_use", "id": "a", "name": "Read", "input": {}}]
        v = turn_guard.inspect(blocks)
        self.assertTrue(v.ok)
        self.assertEqual(v.blocks, blocks)

    def test_stop_sequences(self):
        self.assertIn("\nuser", turn_guard.stop_sequences("prose"))
        self.assertNotIn("\nuser", turn_guard.stop_sequences("code"))
        self.assertEqual(len(turn_guard.stop_sequences("prose", max_n=4)), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
