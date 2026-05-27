from __future__ import annotations

from pathlib import Path
from threading import Event, Thread
import sys
import traceback

from PySide6.QtCore import QObject, QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QIntValidator
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .capture import CaptureCancelled, capture_pages, cleanup_images_after_failure
from .core import (
    CaptureJob,
    CaptureRegion,
    OperationCancelled,
    Point,
    ValidationError,
    build_pdf_from_images,
    list_capture_images,
    make_pdf_path,
    parse_page_count,
    remove_directory,
    unique_image_dir,
)


KEY_CHOICES = {
    "오른쪽 방향키": "right",
    "왼쪽 방향키": "left",
    "아래 방향키": "down",
    "위 방향키": "up",
    "스페이스": "space",
    "Page Down": "page_down",
}


class CoordinatePicker(QObject):
    picked = Signal(str, int, int)
    failed = Signal(str)

    def pick(self, target: str) -> None:
        def runner() -> None:
            try:
                from pynput import mouse

                def on_click(x, y, _button, pressed):
                    if pressed:
                        self.picked.emit(target, int(x), int(y))
                        return False
                    return None

                with mouse.Listener(on_click=on_click) as listener:
                    listener.join()
            except Exception as exc:  # pragma: no cover - depends on desktop session
                self.failed.emit(str(exc))

        Thread(target=runner, daemon=True).start()


class CaptureWorker(QObject):
    status_changed = Signal(str)
    progress_changed = Signal(str, int, int)
    finished = Signal(object, object)
    cancelled = Signal()
    failed = Signal(str)

    def __init__(self, job: CaptureJob) -> None:
        super().__init__()
        self.job = job
        self.cancel_event = Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise OperationCancelled

    def run(self) -> None:
        try:
            captured_paths = capture_pages(
                self.job,
                self.cancel_event,
                lambda current, total: self.progress_changed.emit("capture", current, total),
                self.status_changed.emit,
            )
            if self.cancel_event.is_set():
                raise CaptureCancelled

            self.status_changed.emit("PDF 변환 중...")
            image_paths = captured_paths or list_capture_images(self.job.image_dir)
            build_pdf_from_images(
                image_paths,
                self.job.pdf_path,
                on_progress=lambda current, total: self.progress_changed.emit("pdf", current, total),
                check_cancelled=self.check_cancelled,
            )

            if not self.job.keep_images:
                remove_directory(self.job.image_dir)

            self.finished.emit(self.job.pdf_path, self.job.image_dir if self.job.keep_images else None)
        except (CaptureCancelled, OperationCancelled):
            cleanup_images_after_failure(self.job)
            self.cancelled.emit()
        except Exception:
            cleanup_images_after_failure(self.job)
            self.failed.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.top_left: Point | None = None
        self.bottom_right: Point | None = None
        self.capture_thread: QThread | None = None
        self.capture_worker: CaptureWorker | None = None
        self.close_after_capture_stops = False
        self.coordinate_picker = CoordinatePicker()
        self.coordinate_picker.picked.connect(self.on_coordinate_picked)
        self.coordinate_picker.failed.connect(self.on_coordinate_failed)

        self.setWindowTitle("eBookToPdf")
        self.setMinimumSize(QSize(620, 520))

        icon_path = Path(__file__).with_name("assets") / "favicon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.title_label = QLabel("E-Book PDF 생성기")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = self.title_label.font()
        title_font.setPointSize(20)
        title_font.setBold(True)
        self.title_label.setFont(title_font)

        self.top_left_label = QLabel("(0, 0)")
        self.bottom_right_label = QLabel("(0, 0)")
        self.pick_top_left_button = QPushButton("좌측상단 좌표 클릭")
        self.pick_bottom_right_button = QPushButton("우측하단 좌표 클릭")
        self.pick_top_left_button.clicked.connect(lambda: self.start_coordinate_pick("top_left"))
        self.pick_bottom_right_button.clicked.connect(lambda: self.start_coordinate_pick("bottom_right"))

        coordinates_layout = QGridLayout()
        coordinates_layout.addWidget(QLabel("이미지 좌측상단 좌표"), 0, 0)
        coordinates_layout.addWidget(self.top_left_label, 0, 1)
        coordinates_layout.addWidget(self.pick_top_left_button, 0, 2)
        coordinates_layout.addWidget(QLabel("이미지 우측하단 좌표"), 1, 0)
        coordinates_layout.addWidget(self.bottom_right_label, 1, 1)
        coordinates_layout.addWidget(self.pick_bottom_right_button, 1, 2)
        coordinates_box = QGroupBox("캡처 영역")
        coordinates_box.setLayout(coordinates_layout)

        self.page_count_input = QLineEdit()
        self.page_count_input.setPlaceholderText("예: 120")
        self.page_count_input.setValidator(QIntValidator(1, 99999, self))

        self.pdf_name_input = QLineEdit()
        self.pdf_name_input.setPlaceholderText("생성할 PDF 이름")

        self.output_dir_input = QLineEdit(str(Path.cwd()))
        self.output_dir_input.setReadOnly(True)
        self.output_browse_button = QPushButton("폴더 선택")
        self.output_browse_button.clicked.connect(self.select_output_dir)

        output_row = QHBoxLayout()
        output_row.addWidget(self.output_dir_input, 1)
        output_row.addWidget(self.output_browse_button)

        form_layout = QFormLayout()
        form_layout.addRow("총 페이지 수", self.page_count_input)
        form_layout.addRow("PDF 이름", self.pdf_name_input)
        form_layout.addRow("저장 폴더", output_row)
        document_box = QGroupBox("문서 설정")
        document_box.setLayout(form_layout)

        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setMinimum(1)
        self.speed_slider.setMaximum(50)
        self.speed_slider.setValue(5)
        self.speed_slider.valueChanged.connect(self.update_speed_label)
        self.speed_label = QLabel()
        self.update_speed_label()

        self.start_delay_slider = QSlider(Qt.Orientation.Horizontal)
        self.start_delay_slider.setMinimum(0)
        self.start_delay_slider.setMaximum(100)
        self.start_delay_slider.setValue(20)
        self.start_delay_slider.valueChanged.connect(self.update_start_delay_label)
        self.start_delay_label = QLabel()
        self.update_start_delay_label()

        self.next_key_combo = QComboBox()
        self.next_key_combo.addItems(KEY_CHOICES.keys())

        self.focus_checkbox = QCheckBox("시작 전에 좌측상단을 클릭해서 뷰어 포커스")
        self.focus_checkbox.setChecked(True)
        self.keep_images_checkbox = QCheckBox("PDF 생성 후 캡처 이미지 보관")

        capture_form = QFormLayout()
        capture_form.addRow("캡처 간격", self.speed_label)
        capture_form.addRow("", self.speed_slider)
        capture_form.addRow("시작 대기", self.start_delay_label)
        capture_form.addRow("", self.start_delay_slider)
        capture_form.addRow("페이지 넘김 키", self.next_key_combo)
        capture_form.addRow("", self.focus_checkbox)
        capture_form.addRow("", self.keep_images_checkbox)
        capture_box = QGroupBox("캡처 설정")
        capture_box.setLayout(capture_form)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("대기 중")
        self.status_label = QLabel("대기 중")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_font = self.status_label.font()
        status_font.setPointSize(12)
        status_font.setBold(True)
        self.status_label.setFont(status_font)

        self.make_button = QPushButton("PDF로 만들기")
        self.make_button.setFixedHeight(48)
        self.make_button.clicked.connect(self.start_capture)
        self.cancel_button = QPushButton("중지")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_capture)
        self.reset_button = QPushButton("초기화")
        self.reset_button.clicked.connect(self.reset_form)

        action_layout = QHBoxLayout()
        action_layout.addWidget(self.reset_button)
        action_layout.addWidget(self.cancel_button)
        action_layout.addWidget(self.make_button, 1)

        layout = QVBoxLayout()
        layout.addWidget(self.title_label)
        layout.addWidget(coordinates_box)
        layout.addWidget(document_box)
        layout.addWidget(capture_box)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_label)
        layout.addLayout(action_layout)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    def start_coordinate_pick(self, target: str) -> None:
        self.set_coordinate_buttons_enabled(False)
        label = "좌측상단" if target == "top_left" else "우측하단"
        self.status_label.setText(f"{label} 위치를 마우스로 클릭하세요.")
        self.coordinate_picker.pick(target)

    def on_coordinate_picked(self, target: str, x: int, y: int) -> None:
        point = Point(x, y)
        if target == "top_left":
            self.top_left = point
            self.top_left_label.setText(f"({x}, {y})")
        else:
            self.bottom_right = point
            self.bottom_right_label.setText(f"({x}, {y})")
        self.status_label.setText("좌표가 저장되었습니다.")
        self.set_coordinate_buttons_enabled(True)

    def on_coordinate_failed(self, message: str) -> None:
        self.status_label.setText("좌표 입력 오류")
        self.set_coordinate_buttons_enabled(True)
        QMessageBox.critical(self, "좌표 입력 오류", message)

    def set_coordinate_buttons_enabled(self, enabled: bool) -> None:
        self.pick_top_left_button.setEnabled(enabled)
        self.pick_bottom_right_button.setEnabled(enabled)

    def select_output_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self.output_dir_input.text())
        if selected:
            self.output_dir_input.setText(selected)

    def update_speed_label(self) -> None:
        self.speed_label.setText(f"{self.capture_delay:.1f}초")

    def update_start_delay_label(self) -> None:
        self.start_delay_label.setText(f"{self.start_delay:.1f}초")

    @property
    def capture_delay(self) -> float:
        return self.speed_slider.value() / 10.0

    @property
    def start_delay(self) -> float:
        return self.start_delay_slider.value() / 10.0

    def build_job_from_form(self) -> CaptureJob:
        if self.top_left is None or self.bottom_right is None:
            raise ValidationError("좌측상단과 우측하단 좌표를 모두 선택하세요.")

        region = CaptureRegion.from_points(self.top_left, self.bottom_right)
        page_count = parse_page_count(self.page_count_input.text())
        output_dir = Path(self.output_dir_input.text())
        pdf_path = make_pdf_path(output_dir, self.pdf_name_input.text())
        image_dir = unique_image_dir(output_dir, pdf_path.stem)
        next_key = KEY_CHOICES[self.next_key_combo.currentText()]

        return CaptureJob(
            region=region,
            page_count=page_count,
            pdf_path=pdf_path,
            image_dir=image_dir,
            capture_delay=self.capture_delay,
            start_delay=self.start_delay,
            next_key=next_key,
            keep_images=self.keep_images_checkbox.isChecked(),
            focus_before_capture=self.focus_checkbox.isChecked(),
        )

    def start_capture(self) -> None:
        try:
            job = self.build_job_from_form()
        except ValidationError as exc:
            self.status_label.setText(str(exc))
            return

        if job.pdf_path.exists():
            answer = QMessageBox.question(
                self,
                "PDF 덮어쓰기",
                f"이미 존재하는 파일입니다.\n{job.pdf_path}\n덮어쓸까요?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("0%")
        self.status_label.setText("캡처 준비 중...")
        self.set_busy(True)

        self.capture_thread = QThread(self)
        self.capture_worker = CaptureWorker(job)
        self.capture_worker.moveToThread(self.capture_thread)

        self.capture_thread.started.connect(self.capture_worker.run)
        self.capture_worker.status_changed.connect(self.status_label.setText)
        self.capture_worker.progress_changed.connect(self.on_capture_progress)
        self.capture_worker.finished.connect(self.on_capture_finished)
        self.capture_worker.cancelled.connect(self.on_capture_cancelled)
        self.capture_worker.failed.connect(self.on_capture_failed)

        self.capture_worker.finished.connect(self.capture_thread.quit)
        self.capture_worker.cancelled.connect(self.capture_thread.quit)
        self.capture_worker.failed.connect(self.capture_thread.quit)
        self.capture_thread.finished.connect(self.capture_worker.deleteLater)
        self.capture_thread.finished.connect(self.on_thread_finished)
        self.capture_thread.start()

    def on_capture_progress(self, stage: str, current: int, total: int) -> None:
        if total <= 0:
            self.progress_bar.setValue(0)
            return

        if stage == "capture":
            percent = round(current / total * 80)
            self.status_label.setText(f"캡처 중: {current}/{total} 페이지")
            self.progress_bar.setFormat(f"캡처 {current}/{total} 페이지 - %p%")
        elif stage == "pdf":
            percent = 80 + round(current / total * 19)
            self.status_label.setText(f"PDF 변환 중: {current}/{total} 이미지")
            self.progress_bar.setFormat(f"PDF 변환 {current}/{total} 이미지 - %p%")
        else:
            percent = round(current / total * 100)
            self.progress_bar.setFormat("%p%")

        self.progress_bar.setValue(min(99, max(0, percent)))

    def on_capture_finished(self, pdf_path: Path, image_dir: Path | None) -> None:
        message = f"PDF 변환 완료: {pdf_path}"
        if image_dir is not None:
            message += f"\n이미지 폴더: {image_dir}"
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat("완료")
        self.status_label.setText("PDF 변환 완료!")
        QMessageBox.information(self, "완료", message)

    def on_capture_cancelled(self) -> None:
        self.progress_bar.setFormat("중지됨")
        self.status_label.setText("작업이 취소되었습니다.")

    def on_capture_failed(self, error_text: str) -> None:
        first_line = error_text.strip().splitlines()[-1] if error_text.strip() else "알 수 없는 오류"
        self.progress_bar.setFormat("오류")
        self.status_label.setText("오류 발생")
        QMessageBox.critical(self, "오류 발생", first_line)

    def on_thread_finished(self) -> None:
        self.capture_thread = None
        self.capture_worker = None
        self.set_busy(False)
        if self.close_after_capture_stops:
            self.close_after_capture_stops = False
            self.close()

    def cancel_capture(self) -> None:
        if self.capture_worker is not None:
            self.status_label.setText("중지 요청 중...")
            self.progress_bar.setFormat("중지 요청 중...")
            self.capture_worker.cancel()
            self.cancel_button.setEnabled(False)

    def set_busy(self, busy: bool) -> None:
        for widget in (
            self.pick_top_left_button,
            self.pick_bottom_right_button,
            self.page_count_input,
            self.pdf_name_input,
            self.output_browse_button,
            self.speed_slider,
            self.start_delay_slider,
            self.next_key_combo,
            self.focus_checkbox,
            self.keep_images_checkbox,
            self.reset_button,
        ):
            widget.setEnabled(not busy)
        self.make_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def reset_form(self) -> None:
        if self.capture_worker is not None:
            return
        self.top_left = None
        self.bottom_right = None
        self.top_left_label.setText("(0, 0)")
        self.bottom_right_label.setText("(0, 0)")
        self.page_count_input.clear()
        self.pdf_name_input.clear()
        self.speed_slider.setValue(5)
        self.start_delay_slider.setValue(20)
        self.next_key_combo.setCurrentIndex(0)
        self.focus_checkbox.setChecked(True)
        self.keep_images_checkbox.setChecked(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("대기 중")
        self.status_label.setText("대기 중")

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.capture_worker is None:
            event.accept()
            return

        answer = QMessageBox.question(self, "작업 취소", "캡처 작업을 취소하고 종료할까요?")
        if answer == QMessageBox.StandardButton.Yes:
            self.close_after_capture_stops = True
            self.capture_worker.cancel()
            event.ignore()
        else:
            event.ignore()


def run() -> int:
    enable_windows_dpi_awareness()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


def enable_windows_dpi_awareness() -> None:
    if sys.platform != "win32":
        return

    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
