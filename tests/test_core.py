from pathlib import Path
import tempfile
import unittest

from ebook_to_pdf.core import (
    CaptureRegion,
    Point,
    ValidationError,
    list_capture_images,
    make_pdf_path,
    parse_page_count,
    sanitize_pdf_stem,
    unique_image_dir,
)


class CoreTest(unittest.TestCase):
    def test_parse_page_count_accepts_positive_integer(self) -> None:
        self.assertEqual(parse_page_count("12"), 12)

    def test_parse_page_count_rejects_empty_or_zero(self) -> None:
        with self.assertRaises(ValidationError):
            parse_page_count("")
        with self.assertRaises(ValidationError):
            parse_page_count("0")

    def test_capture_region_requires_bottom_right(self) -> None:
        region = CaptureRegion.from_points(Point(10, 20), Point(110, 220))
        self.assertEqual(region.as_mss_monitor(), {"left": 10, "top": 20, "width": 100, "height": 200})

        with self.assertRaises(ValidationError):
            CaptureRegion.from_points(Point(10, 20), Point(9, 220))

    def test_pdf_name_is_windows_safe(self) -> None:
        self.assertEqual(sanitize_pdf_stem("my/book?.pdf"), "my_book_")
        self.assertEqual(sanitize_pdf_stem("CON"), "CON_ebook")

    def test_make_pdf_path_adds_suffix(self) -> None:
        path = make_pdf_path(Path("/tmp"), "sample.pdf")
        self.assertEqual(path.name, "sample.pdf")

    def test_unique_image_dir_avoids_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "book_images").mkdir()
            self.assertEqual(unique_image_dir(base, "book").name, "book_images_2")

    def test_list_capture_images_ignores_non_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "img_0002.png").touch()
            (base / "img_0001.png").touch()
            (base / "notes.txt").touch()
            self.assertEqual([path.name for path in list_capture_images(base)], ["img_0001.png", "img_0002.png"])


if __name__ == "__main__":
    unittest.main()
