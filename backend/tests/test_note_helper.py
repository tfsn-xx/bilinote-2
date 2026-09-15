import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "app" / "utils" / "note_helper.py"
spec = importlib.util.spec_from_file_location("note_helper", MODULE_PATH)
if spec is None or spec.loader is None:
    raise ImportError("note_helper module spec not found")
note_helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(note_helper)


class TestNoteHelper(unittest.TestCase):
    def test_prepend_source_link_adds_header_at_top(self):
        source_url = "https://www.bilibili.com/video/BV1xx411c7mD"
        markdown = "## 标题\n\n内容"

        result = note_helper.prepend_source_link(markdown, source_url)

        self.assertTrue(result.startswith(f"> 来源链接：{source_url}\n\n"))
        self.assertIn("## 标题", result)

    def test_prepend_source_link_does_not_duplicate_when_header_exists(self):
        source_url = "https://www.youtube.com/watch?v=abc123"
        markdown = f"> 来源链接：{source_url}\n\n## 标题\n\n内容"

        result = note_helper.prepend_source_link(markdown, source_url)

        self.assertEqual(result, markdown)

    def test_replace_content_marker_uses_bilibili_timestamp_link(self):
        markdown = "## 一、五代乱象 *Content-[00:06]"

        result = note_helper.replace_content_markers(markdown, "BV1test123456", "bilibili")

        self.assertEqual(
            result,
            "## 一、五代乱象 [原片 @ 00:06](https://www.bilibili.com/video/BV1test123456?t=6)",
        )
        self.assertNotIn("Content-[00:06]", result)

    def test_replace_content_marker_handles_bilibili_part_id(self):
        markdown = "## 第二部分 Content-01:32"

        result = note_helper.replace_content_markers(markdown, "BV1test123456_p2", "bilibili")

        self.assertIn(
            "[原片 @ 01:32](https://www.bilibili.com/video/BV1test123456?p=2&t=92)",
            result,
        )

    def test_replace_content_marker_handles_multiple_markers(self):
        markdown = "## 第一节 *Content-[00:06]*\n## 第二节 *Content-[01:32]*"

        result = note_helper.replace_content_markers(markdown, "BV1test123456", "bilibili")

        self.assertNotIn("Content-", result)
        self.assertEqual(result.count("https://www.bilibili.com/video/BV1test123456"), 2)
        self.assertNotIn(")*", result)

    def test_replace_content_marker_handles_optional_trailing_star(self):
        markdown = "## 第一节 Content-[00:06]*"

        result = note_helper.replace_content_markers(markdown, "BV1test123456", "bilibili")

        self.assertTrue(result.endswith("?t=6)"))


if __name__ == "__main__":
    unittest.main()
