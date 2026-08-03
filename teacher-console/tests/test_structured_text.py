#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path

CONSOLE_ROOT = Path(__file__).resolve().parents[1]
if str(CONSOLE_ROOT) not in sys.path:
    sys.path.insert(0, str(CONSOLE_ROOT))

import structured_text


class StructuredTextTest(unittest.TestCase):
    def test_allows_normal_multiline_physics_text(self):
        value = "第一行\n第二行\r\nE_k=mv²/2"
        self.assertEqual(
            structured_text.reject_unsupported_controls(value, "field"),
            value,
        )

    def test_rejects_hidden_c0_and_del_controls(self):
        for control in ("\x00", "\x11", "\x12", "\x1e", "\x7f"):
            with self.subTest(code=ord(control)):
                with self.assertRaisesRegex(ValueError, "unsupported control"):
                    structured_text.reject_unsupported_controls(f"正确{control}结论", "field")
