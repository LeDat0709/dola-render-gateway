"""Unit tests for DomixHub-Seedance enhancements."""
import unittest
from facebook_login import extract_fb_credential, parse_fb_cookies, _has_fb_session
from video_worker_ui import (
    CONTENT_POLICY_PATTERN,
    PORTRAIT_PROTECTION_PATTERN,
    PARAMETER_CHANGE_PATTERN,
    DAILY_LIMIT_PATTERN,
)
from cookie_service import parse_cookie_input


class TestDomixHubEnhancements(unittest.TestCase):

    def test_extract_fb_credential(self):
        line = "61594178784700|mqXptX4w||datr=RC2Yau;c_user=61594178784700;xs=2:oxH_m0vpKtlr4w;fr=2xa3z;wd=1080x1920;|||Mozilla/5.0 (Linux; Android 9)"
        cred = extract_fb_credential(line)
        self.assertEqual(cred["uid"], "61594178784700")
        self.assertEqual(cred["password"], "mqXptX4w")
        self.assertIn("c_user=61594178784700", cred["cookie"])
        self.assertIn("Mozilla", cred["user_agent"])

        cookies = parse_fb_cookies(cred["cookie"])
        self.assertTrue(_has_fb_session(cookies))

    def test_content_policy_pattern(self):
        violation_texts = [
            "動画の生成はできません。規約に違反しています。",
            "生成できません: 暴力的な内容が含まれています",
            "This video cannot be generated as it violates our policy",
            "违反社区内容规范，无法生成视频",
        ]
        for txt in violation_texts:
            self.assertIsNotNone(CONTENT_POLICY_PATTERN.search(txt), f"Failed to match policy violation: {txt}")

    def test_portrait_protection_pattern(self):
        portrait_texts = [
            "肖像保護のため生成できません",
            "あなた自身が写っている動画のみ生成可能です",
            "別の画像を使用してください",
            "Portrait protection triggered: can only generate videos of yourself",
            "肖像权保护：请使用您本人的照片",
        ]
        for txt in portrait_texts:
            self.assertIsNotNone(PORTRAIT_PROTECTION_PATTERN.search(txt), f"Failed to match portrait protection: {txt}")

    def test_parameter_change_pattern(self):
        param_texts = [
            "パラメーターを変更してもう一度お試しください",
            "更改参数后重试",
            "Please change the parameters and try again",
        ]
        for txt in param_texts:
            self.assertIsNotNone(PARAMETER_CHANGE_PATTERN.search(txt), f"Failed to match parameter change: {txt}")

    def test_daily_limit_pattern(self):
        limit_texts = [
            "動画生成の1日あたりの上限に達しました",
            "今日生成额度已达到上限",
            "You have reached your daily limit for video generation",
        ]
        for txt in limit_texts:
            self.assertIsNotNone(DAILY_LIMIT_PATTERN.search(txt), f"Failed to match daily limit: {txt}")

    def test_cookie_service_fb_auto_detect(self):
        raw_fb = "datr=abc; c_user=123456; xs=secval; sb=xyz"
        parsed = parse_cookie_input(raw_fb)
        has_sid = any(c["name"] == "sessionid" for c in parsed)
        has_fb = any(c["name"] in ("c_user", "xs") for c in parsed)
        self.assertFalse(has_sid)
        self.assertTrue(has_fb)


if __name__ == "__main__":
    unittest.main()
