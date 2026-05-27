from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
from typing import Callable, Iterable


INVALID_WINDOWS_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class ValidationError(ValueError):
    """Raised when user-provided capture settings are invalid."""


class OperationCancelled(RuntimeError):
    """Raised when a long-running operation is cancelled."""


@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True)
class CaptureRegion:
    left: int
    top: int
    width: int
    height: int

    @classmethod
    def from_points(cls, top_left: Point, bottom_right: Point) -> "CaptureRegion":
        width = bottom_right.x - top_left.x
        height = bottom_right.y - top_left.y
        if width <= 0 or height <= 0:
            raise ValidationError("우측하단 좌표는 좌측상단 좌표보다 오른쪽 아래에 있어야 합니다.")
        return cls(left=top_left.x, top=top_left.y, width=width, height=height)

    def as_mss_monitor(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class CaptureJob:
    region: CaptureRegion
    page_count: int
    pdf_path: Path
    image_dir: Path
    capture_delay: float
    start_delay: float
    next_key: str
    keep_images: bool
    focus_before_capture: bool


def parse_page_count(raw_value: str) -> int:
    value = raw_value.strip()
    if not value:
        raise ValidationError("총 페이지 수를 입력하세요.")

    try:
        page_count = int(value)
    except ValueError as exc:
        raise ValidationError("총 페이지 수는 1 이상의 정수여야 합니다.") from exc

    if page_count < 1:
        raise ValidationError("총 페이지 수는 1 이상이어야 합니다.")

    return page_count


def sanitize_pdf_stem(raw_name: str) -> str:
    name = raw_name.strip()
    if not name:
        raise ValidationError("PDF 이름을 입력하세요.")

    if name.lower().endswith(".pdf"):
        name = name[:-4].strip()

    name = INVALID_WINDOWS_NAME_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        raise ValidationError("PDF 이름에 사용할 수 있는 문자가 없습니다.")

    if name.upper() in RESERVED_WINDOWS_NAMES:
        name = f"{name}_ebook"

    return name


def make_pdf_path(output_dir: Path, raw_name: str) -> Path:
    return output_dir.expanduser().resolve() / f"{sanitize_pdf_stem(raw_name)}.pdf"


def unique_image_dir(base_dir: Path, stem: str) -> Path:
    clean_stem = sanitize_pdf_stem(stem)
    base = base_dir.expanduser().resolve() / f"{clean_stem}_images"
    candidate = base
    index = 2
    while candidate.exists():
        candidate = base.with_name(f"{base.name}_{index}")
        index += 1
    return candidate


def list_capture_images(image_dir: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in image_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}
        ),
        key=lambda path: path.name.lower(),
    )


def build_pdf_from_images(
    image_paths: Iterable[Path],
    pdf_path: Path,
    quality: int = 100,
    on_progress: Callable[[int, int], None] | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> None:
    paths = list(image_paths)
    if not paths:
        raise ValidationError("PDF로 변환할 캡처 이미지가 없습니다.")

    from PIL import Image

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    temp_pdf_path = pdf_path.with_name(f".{pdf_path.stem}.tmp.pdf")
    if temp_pdf_path.exists():
        temp_pdf_path.unlink()

    opened_images = []
    try:
        for index, path in enumerate(paths, start=1):
            if check_cancelled is not None:
                check_cancelled()
            with Image.open(path) as image:
                opened_images.append(image.convert("RGB"))
            if on_progress is not None:
                on_progress(index, len(paths))

        if check_cancelled is not None:
            check_cancelled()
        first_page, *remaining_pages = opened_images
        first_page.save(
            temp_pdf_path,
            save_all=True,
            append_images=remaining_pages,
            quality=quality,
            resolution=100.0,
        )
        temp_pdf_path.replace(pdf_path)
    finally:
        for image in opened_images:
            image.close()
        if temp_pdf_path.exists():
            temp_pdf_path.unlink()


def remove_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
