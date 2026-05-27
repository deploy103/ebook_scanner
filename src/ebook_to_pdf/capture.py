from __future__ import annotations

from pathlib import Path
from threading import Event
import time
from typing import Callable

from .core import CaptureJob, remove_directory


ProgressCallback = Callable[[int, int], None]
StatusCallback = Callable[[str], None]


class CaptureCancelled(RuntimeError):
    """Raised when the user cancels an active capture job."""


def sleep_with_cancel(seconds: float, cancel_event: Event) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        if cancel_event.is_set():
            raise CaptureCancelled
        time.sleep(min(0.05, deadline - time.monotonic()))


def resolve_keyboard_key(key_name: str):
    from pynput.keyboard import Key

    keys = {
        "right": Key.right,
        "left": Key.left,
        "down": Key.down,
        "up": Key.up,
        "space": Key.space,
        "page_down": Key.page_down,
    }
    return keys[key_name]


def capture_pages(
    job: CaptureJob,
    cancel_event: Event,
    on_progress: ProgressCallback,
    on_status: StatusCallback,
) -> list[Path]:
    import mss
    import mss.tools
    from pynput import mouse
    from pynput.keyboard import Controller

    job.image_dir.mkdir(parents=True, exist_ok=True)

    mouse_controller = mouse.Controller()
    keyboard = Controller()
    next_key = resolve_keyboard_key(job.next_key)
    original_mouse_position = mouse_controller.position

    if job.focus_before_capture:
        on_status("캡처할 화면을 포커스하는 중...")
        sleep_with_cancel(job.start_delay, cancel_event)
        mouse_controller.position = (job.region.left, job.region.top)
        mouse_controller.click(mouse.Button.left)
        sleep_with_cancel(0.4, cancel_event)
        mouse_controller.position = original_mouse_position
    else:
        on_status("캡처 시작 대기 중...")
        sleep_with_cancel(job.start_delay, cancel_event)

    captured_paths: list[Path] = []
    monitor = job.region.as_mss_monitor()

    with mss.mss() as screen_capture:
        for page in range(1, job.page_count + 1):
            if cancel_event.is_set():
                raise CaptureCancelled

            on_status(f"{page}/{job.page_count} 페이지 캡처 중...")
            sleep_with_cancel(job.capture_delay, cancel_event)

            image = screen_capture.grab(monitor)
            image_path = job.image_dir / f"img_{page:04d}.png"
            mss.tools.to_png(image.rgb, image.size, output=str(image_path))
            captured_paths.append(image_path)
            on_progress(page, job.page_count)

            if page < job.page_count:
                keyboard.press(next_key)
                keyboard.release(next_key)

    return captured_paths


def cleanup_images_after_failure(job: CaptureJob) -> None:
    if not job.keep_images:
        remove_directory(job.image_dir)
