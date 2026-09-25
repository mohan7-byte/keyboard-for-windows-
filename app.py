import sys
import time
import json
import urllib.request
import ctypes
from ctypes import wintypes

from PyQt6.QtCore import Qt, QPoint, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QPainter, QPen, QColor, QFont
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSizeGrip, QCheckBox
)

# ----------------- Windows Win32 API Hooks -----------------
user32 = ctypes.windll.user32

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
WM_MOUSEACTIVATE = 0x0021
MA_NOACTIVATE = 3

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_BACK = 0x08
VK_RETURN = 0x0D
VK_SPACE = 0x20

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]
    _anonymous_ = ("_input",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUT),
    ]

def send_unicode_char(char):
    """Sends a single Unicode character without clipboard injection."""
    code = ord(char)
    inp1 = INPUT(type=INPUT_KEYBOARD)
    inp1.ki = KEYBDINPUT(wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=None)
    inp2 = INPUT(type=INPUT_KEYBOARD)
    inp2.ki = KEYBDINPUT(wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)
    user32.SendInput(2, (INPUT * 2)(inp1, inp2), ctypes.sizeof(INPUT))

def send_vk_key(vk_code):
    """Sends physical virtual key codes like Backspace, Space, Enter."""
    inp1 = INPUT(type=INPUT_KEYBOARD)
    inp1.ki = KEYBDINPUT(wVk=vk_code, wScan=0, dwFlags=0, time=0, dwExtraInfo=None)
    inp2 = INPUT(type=INPUT_KEYBOARD)
    inp2.ki = KEYBDINPUT(wVk=vk_code, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)
    user32.SendInput(2, (INPUT * 2)(inp1, inp2), ctypes.sizeof(INPUT))

def type_text(text):
    """Types a full string character-by-character."""
    for ch in text:
        send_unicode_char(ch)
        time.sleep(0.005)

def press_backspace(count=1):
    for _ in range(count):
        send_vk_key(VK_BACK)
        time.sleep(0.005)


# ----------------- Handwriting Worker Thread -----------------
class RecognitionWorker(QThread):
    finished = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, strokes, width, height, lang="hi"):
        super().__init__()
        self.strokes = strokes
        self.width = width
        self.height = height
        self.lang = lang

    def run(self):
        payload = {
            "options": "enable_pre_space",
            "requests": [{
                "writing_guide": {
                    "writing_area_width": max(self.width, 300),
                    "writing_area_height": max(self.height, 200)
                },
                "ink": self.strokes,
                "language": self.lang
            }]
        }

        try:
            req = urllib.request.Request(
                "https://inputtools.google.com/request?ime=handwriting&app=mobilesearch&cs=1&oe=UTF-8",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
                if result and result[0] == "SUCCESS" and len(result) > 1 and len(result[1]) > 0:
                    candidates = result[1][0][1]
                    self.finished.emit(candidates)
                else:
                    self.failed.emit("No match")
        except Exception as e:
            self.failed.emit(str(e))


# ----------------- Drawing Pad Widget -----------------
class InkPad(QWidget):
    stroke_finished = pyqtSignal(list, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.strokes = []
        self.current_stroke = {"x": [], "y": [], "t": []}
        self.start_time = 0
        self.is_drawing = False

        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.setInterval(650)  # 650ms after lifting pen triggers recognition
        self.idle_timer.timeout.connect(self._on_idle_timeout)

        self.setStyleSheet("background-color: #0f172a; border-radius: 8px;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.idle_timer.stop()
            self.is_drawing = True
            pos = event.position()
            if not self.strokes:
                self.start_time = time.time()
            t = int((time.time() - self.start_time) * 1000)
            self.current_stroke = {"x": [pos.x()], "y": [pos.y()], "t": [t]}
            self.update()

    def mouseMoveEvent(self, event):
        if self.is_drawing:
            pos = event.position()
            t = int((time.time() - self.start_time) * 1000)
            self.current_stroke["x"].append(pos.x())
            self.current_stroke["y"].append(pos.y())
            self.current_stroke["t"].append(t)
            self.update()

    def mouseReleaseEvent(self, event):
        if self.is_drawing and event.button() == Qt.MouseButton.LeftButton:
            self.is_drawing = False
            if len(self.current_stroke["x"]) > 0:
                self.strokes.append(self.current_stroke)
            self.update()
            self.idle_timer.start()

    def _on_idle_timeout(self):
        if self.strokes:
            ink_payload = [[s["x"], s["y"], s["t"]] for s in self.strokes]
            self.stroke_finished.emit(ink_payload, self.width(), self.height())

    def clear(self):
        self.idle_timer.stop()
        self.strokes = []
        self.current_stroke = {"x": [], "y": [], "t": []}
        self.update()

    def undo(self):
        if self.strokes:
            self.strokes.pop()
            self.update()
            if self.strokes:
                self.idle_timer.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen = QPen(QColor("#38bdf8"), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        for stroke in self.strokes:
            xs, ys = stroke["x"], stroke["y"]
            for i in range(1, len(xs)):
                painter.drawLine(int(xs[i - 1]), int(ys[i - 1]), int(xs[i]), int(ys[i]))

        if self.is_drawing and len(self.current_stroke["x"]) > 1:
            xs, ys = self.current_stroke["x"], self.current_stroke["y"]
            for i in range(1, len(xs)):
                painter.drawLine(int(xs[i - 1]), int(ys[i - 1]), int(xs[i]), int(ys[i]))


# ----------------- Main Floating Keyboard Window -----------------
class FloatingHandwritingKeyboard(QWidget):
    def __init__(self):
        super().__init__()
        self.current_lang = "hi"
        self.last_typed_text = ""
        self.drag_position = QPoint()

        self.init_window_flags()
        self.init_ui()

    def init_window_flags(self):
        # Frameless, Always On Top
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(420, 260)
        self.resize(540, 320)

    def showEvent(self, event):
        super().showEvent(event)
        # Apply Windows Non-Activating extended style
        hwnd = int(self.winId())
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOPMOST)

    def nativeEvent(self, eventType, message):
        """Prevent activating this window when touched/clicked so the target app keeps cursor focus."""
        if eventType == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(message.__int__())
            if msg.message == WM_MOUSEACTIVATE:
                return True, MA_NOACTIVATE
        return super().nativeEvent(eventType, message)

    def init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)

        # Main background container
        self.container = QWidget(self)
        self.container.setStyleSheet("""
            QWidget {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 14px;
                color: #f8fafc;
            }
        """)
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(10, 8, 10, 8)
        container_layout.setSpacing(6)

        # Top Drag & Control Bar
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(4, 0, 4, 0)

        self.title_lbl = QLabel("✍️ Ink Keyboard", self)
        self.title_lbl.setStyleSheet("font-weight: 700; font-size: 13px; color: #94a3b8; border: none;")

        self.lang_btn = QPushButton("HI (Hindi)", self)
        self.lang_btn.setFixedSize(85, 26)
        self.lang_btn.setStyleSheet("""
            QPushButton {
                background: #3b82f6; color: white; font-weight: 600; border-radius: 6px; border: none; font-size: 11px;
            }
            QPushButton:hover { background: #2563eb; }
        """)
        self.lang_btn.clicked.connect(self.toggle_language)

        self.auto_type_cb = QCheckBox("Auto-Type", self)
        self.auto_type_cb.setChecked(True)
        self.auto_type_cb.setStyleSheet("font-size: 11px; color: #94a3b8; border: none;")

        btn_style = """
            QPushButton {
                background: #334155; color: #f8fafc; border-radius: 5px; border: 1px solid #475569;
                font-size: 11px; padding: 3px 8px; font-weight: 600;
            }
            QPushButton:hover { background: #475569; }
        """

        self.undo_btn = QPushButton("↩ Undo", self)
        self.undo_btn.setStyleSheet(btn_style)
        self.clear_btn = QPushButton("🗑 Clear", self)
        self.clear_btn.setStyleSheet(btn_style)

        self.bksp_btn = QPushButton("⌫ Back", self)
        self.bksp_btn.setStyleSheet(btn_style)
        self.space_btn = QPushButton("␣ Space", self)
        self.space_btn.setStyleSheet(btn_style)
        self.enter_btn = QPushButton("⏎ Enter", self)
        self.enter_btn.setStyleSheet(btn_style)

        self.close_btn = QPushButton("✕", self)
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setStyleSheet("""
            QPushButton { background: #ef4444; color: white; border-radius: 12px; border: none; font-weight: bold; }
            QPushButton:hover { background: #dc2626; }
        """)
        self.close_btn.clicked.connect(self.close)

        top_bar.addWidget(self.title_lbl)
        top_bar.addWidget(self.lang_btn)
        top_bar.addWidget(self.auto_type_cb)
        top_bar.addStretch()
        top_bar.addWidget(self.undo_btn)
        top_bar.addWidget(self.clear_btn)
        top_bar.addWidget(self.bksp_btn)
        top_bar.addWidget(self.space_btn)
        top_bar.addWidget(self.enter_btn)
        top_bar.addWidget(self.close_btn)
        container_layout.addLayout(top_bar)

        # Drawing Canvas
        self.canvas = InkPad(self)
        self.canvas.stroke_finished.connect(self.recognize_strokes)
        container_layout.addWidget(self.canvas, 1)

        # Candidate Suggestions Bar + Resize Grip
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(4, 2, 0, 2)

        self.cand_container = QHBoxLayout()
        self.cand_container.setSpacing(6)
        bottom_bar.addLayout(self.cand_container, 1)

        self.size_grip = QSizeGrip(self)
        self.size_grip.setStyleSheet("background: transparent; border: none;")
        bottom_bar.addWidget(self.size_grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        container_layout.addLayout(bottom_bar)
        root_layout.addWidget(self.container)

        # Button connections
        self.undo_btn.clicked.connect(self.canvas.undo)
        self.clear_btn.clicked.connect(self.clear_all)
        self.bksp_btn.clicked.connect(lambda: press_backspace(1))
        self.space_btn.clicked.connect(lambda: type_text(" "))
        self.enter_btn.clicked.connect(lambda: send_vk_key(VK_RETURN))

    def toggle_language(self):
        if self.current_lang == "hi":
            self.current_lang = "en"
            self.lang_btn.setText("EN (English)")
            self.lang_btn.setStyleSheet("background: #10b981; color: white; font-weight: 600; border-radius: 6px; border: none;")
        else:
            self.current_lang = "hi"
            self.lang_btn.setText("HI (Hindi)")
            self.lang_btn.setStyleSheet("background: #3b82f6; color: white; font-weight: 600; border-radius: 6px; border: none;")

    def recognize_strokes(self, strokes, w, h):
        self.worker = RecognitionWorker(strokes, w, h, self.current_lang)
        self.worker.finished.connect(self.on_recognized)
        self.worker.failed.connect(lambda msg: print("Recognition failed:", msg))
        self.worker.start()

    def on_recognized(self, candidates):
        # Clear existing candidate chips
        while self.cand_container.count():
            item = self.cand_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not candidates:
            return

        top_word = candidates[0]

        # Auto-type top word if enabled
        if self.auto_type_cb.isChecked():
            type_text(top_word + " ")
            self.last_typed_text = top_word + " "
            self.canvas.clear()

        # Display candidates as clickable replacement chips
        for word in candidates[:6]:
            chip = QPushButton(word, self)
            chip.setStyleSheet("""
                QPushButton {
                    background: #0f172a; border: 1px solid #475569; color: #38bdf8;
                    border-radius: 12px; padding: 3px 10px; font-weight: 600; font-size: 12px;
                }
                QPushButton:hover { background: #334155; color: #ffffff; }
            """)
            chip.clicked.connect(lambda checked, w=word: self.on_candidate_clicked(w))
            self.cand_container.addWidget(chip)

    def on_candidate_clicked(self, word):
        if self.last_typed_text:
            press_backspace(len(self.last_typed_text))
        type_text(word + " ")
        self.last_typed_text = word + " "
        self.canvas.clear()

    def clear_all(self):
        self.canvas.clear()
        while self.cand_container.count():
            item = self.cand_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # Frameless Window Dragging
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and not self.drag_position.isNull():
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_position = QPoint()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 9))
    keyboard = FloatingHandwritingKeyboard()
    keyboard.show()
    sys.exit(app.exec())
