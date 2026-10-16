"""
Dark theme QSS stylesheet for the application.
Professional dark mode with teal/blue accent colors.
"""

DARK_THEME = """
/* ── Global ────────────────────────────────────────────────────── */
QWidget {
    background-color: #1a1a2e;
    color: #e0e0e8;
    font-family: "Segoe UI", "Arial", sans-serif;
    font-size: 11px;
}

QMainWindow {
    background-color: #1a1a2e;
}

/* ── Menu Bar ──────────────────────────────────────────────────── */
QMenuBar {
    background-color: #16213e;
    color: #e0e0e8;
    border-bottom: 1px solid #0f3460;
    padding: 2px;
}

QMenuBar::item {
    background: transparent;
    padding: 6px 12px;
    border-radius: 4px;
}

QMenuBar::item:selected {
    background-color: #0f3460;
}

QMenu {
    background-color: #16213e;
    border: 1px solid #0f3460;
    border-radius: 6px;
    padding: 4px;
}

QMenu::item {
    padding: 8px 24px 8px 12px;
    border-radius: 4px;
}

QMenu::item:selected {
    background-color: #0f3460;
}

QMenu::separator {
    height: 1px;
    background-color: #0f3460;
    margin: 4px 8px;
}

/* ── Tab Widget ────────────────────────────────────────────────── */
QTabWidget::pane {
    background-color: #16213e;
    border: 1px solid #0f3460;
    border-radius: 8px;
    top: -1px;
}

QTabBar::tab {
    background-color: #1a1a2e;
    color: #8888a0;
    padding: 10px 20px;
    margin-right: 2px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    border: 1px solid transparent;
    border-bottom: none;
    min-width: 120px;
}

QTabBar::tab:selected {
    background-color: #16213e;
    color: #00d4aa;
    border: 1px solid #0f3460;
    border-bottom: 2px solid #00d4aa;
    font-weight: bold;
}

QTabBar::tab:hover:!selected {
    background-color: #0f3460;
    color: #c0c0d0;
}

/* ── Buttons ───────────────────────────────────────────────────── */
QPushButton {
    background-color: #0f3460;
    color: #e0e0e8;
    border: 1px solid #1a5276;
    border-radius: 6px;
    padding: 8px 18px;
    font-weight: 500;
    min-height: 20px;
}

QPushButton:hover {
    background-color: #1a5276;
    border-color: #00d4aa;
}

QPushButton:pressed {
    background-color: #0a2240;
}

QPushButton:disabled {
    background-color: #2a2a3e;
    color: #555570;
    border-color: #2a2a3e;
}

QPushButton#btn_primary {
    background-color: #00d4aa;
    color: #1a1a2e;
    font-weight: bold;
    border: none;
}

QPushButton#btn_primary:hover {
    background-color: #00e8bb;
}

QPushButton#btn_primary:pressed {
    background-color: #00b894;
}

QPushButton#btn_danger {
    background-color: #e74c3c;
    color: white;
    border: none;
}

QPushButton#btn_danger:hover {
    background-color: #ff6b6b;
}

QPushButton#btn_success {
    background-color: #00b894;
    color: white;
    border: none;
    font-weight: bold;
}

QPushButton#btn_success:hover {
    background-color: #00d4aa;
}

/* ── Tables ────────────────────────────────────────────────────── */
QTableWidget, QTableView {
    background-color: #16213e;
    alternate-background-color: #1a2744;
    gridline-color: #0f3460;
    border: 1px solid #0f3460;
    border-radius: 6px;
    selection-background-color: #0f3460;
    selection-color: #00d4aa;
}

QTableWidget::item, QTableView::item {
    padding: 4px 8px;
    border: none;
}

QTableWidget::item:selected, QTableView::item:selected {
    background-color: #0f3460;
    color: #00d4aa;
}

QHeaderView::section {
    background-color: #0f3460;
    color: #00d4aa;
    padding: 8px 6px;
    border: none;
    border-right: 1px solid #1a5276;
    border-bottom: 2px solid #00d4aa;
    font-weight: bold;
    font-size: 12px;
}

QHeaderView::section:hover {
    background-color: #1a5276;
}

/* ── Input Fields ──────────────────────────────────────────────── */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #0f3460;
    color: #e0e0e8;
    border: 1px solid #1a5276;
    border-radius: 6px;
    padding: 6px 10px;
    min-height: 20px;
}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: #00d4aa;
}

QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 20px;
    border-left: 1px solid #1a5276;
    border-bottom: 1px solid #1a5276;
    border-top-right-radius: 5px;
    background-color: #1a5276;
}

QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 20px;
    border-left: 1px solid #1a5276;
    border-bottom-right-radius: 5px;
    background-color: #1a5276;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border-left: 1px solid #1a5276;
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
    background-color: #1a5276;
}

QComboBox QAbstractItemView {
    background-color: #16213e;
    border: 1px solid #0f3460;
    selection-background-color: #0f3460;
    selection-color: #00d4aa;
}

/* ── Checkboxes ────────────────────────────────────────────────── */
QCheckBox {
    spacing: 8px;
    color: #e0e0e8;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid #1a5276;
    border-radius: 4px;
    background-color: #0f3460;
}

QCheckBox::indicator:checked {
    background-color: #00d4aa;
    border-color: #00d4aa;
}

QCheckBox::indicator:hover {
    border-color: #00d4aa;
}

/* ── Group Boxes ───────────────────────────────────────────────── */
QGroupBox {
    font-weight: bold;
    color: #00d4aa;
    border: 1px solid #0f3460;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 16px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 4px 12px;
    background-color: #16213e;
    border-radius: 4px;
    left: 12px;
}

/* ── Progress Bar ──────────────────────────────────────────────── */
QProgressBar {
    background-color: #0f3460;
    border: none;
    border-radius: 8px;
    text-align: center;
    color: #e0e0e8;
    min-height: 24px;
    font-weight: bold;
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #00d4aa, stop:1 #0abde3);
    border-radius: 8px;
}

/* ── Scroll Bars ───────────────────────────────────────────────── */
QScrollBar:vertical {
    background: #1a1a2e;
    width: 10px;
    border-radius: 5px;
}

QScrollBar::handle:vertical {
    background: #0f3460;
    border-radius: 5px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background: #1a5276;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    background: #1a1a2e;
    height: 10px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal {
    background: #0f3460;
    border-radius: 5px;
    min-width: 30px;
}

QScrollBar::handle:horizontal:hover {
    background: #1a5276;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

/* ── Scroll Areas ──────────────────────────────────────────────── */
QScrollArea {
    background: transparent;
    border: none;
}

QScrollArea > QWidget > QWidget {
    background: transparent;
}

/* ── Labels ────────────────────────────────────────────────────── */
QLabel {
    color: #e0e0e8;
}

QLabel#label_title {
    font-size: 15px;
    font-weight: bold;
    color: #00d4aa;
}

QLabel#label_subtitle {
    font-size: 12px;
    color: #8888a0;
}

QLabel#label_stat {
    font-size: 24px;
    font-weight: bold;
    color: #00d4aa;
}

QLabel#label_warning {
    color: #f39c12;
    font-weight: bold;
}

QLabel#label_error {
    color: #e74c3c;
    font-weight: bold;
}

QLabel#label_success {
    color: #00b894;
    font-weight: bold;
}

/* ── Splitter ──────────────────────────────────────────────────── */
QSplitter::handle {
    background-color: #0f3460;
    width: 2px;
    height: 2px;
}

/* ── Tool Tips ─────────────────────────────────────────────────── */
QToolTip {
    background-color: #16213e;
    color: #e0e0e8;
    border: 1px solid #00d4aa;
    border-radius: 4px;
    padding: 6px;
}

/* ── Dialogs ───────────────────────────────────────────────────── */
QDialog {
    background-color: #1a1a2e;
}

/* ── Status Bar ────────────────────────────────────────────────── */
QStatusBar {
    background-color: #16213e;
    color: #8888a0;
    border-top: 1px solid #0f3460;
}

QStatusBar::item {
    border: none;
}

/* ── Text Edit ─────────────────────────────────────────────────── */
QTextEdit, QPlainTextEdit {
    background-color: #0f3460;
    color: #e0e0e8;
    border: 1px solid #1a5276;
    border-radius: 6px;
    padding: 6px;
}

/* ── Tree Widget ───────────────────────────────────────────────── */
QTreeWidget {
    background-color: #16213e;
    alternate-background-color: #1a2744;
    border: 1px solid #0f3460;
    border-radius: 6px;
}

QTreeWidget::item {
    padding: 4px;
}

QTreeWidget::item:selected {
    background-color: #0f3460;
    color: #00d4aa;
}

QTreeWidget::branch {
    background-color: transparent;
}
"""


# Color constants for programmatic use
class Colors:
    BG_PRIMARY = "#1a1a2e"
    BG_SECONDARY = "#16213e"
    BG_INPUT = "#0f3460"
    ACCENT = "#00d4aa"
    ACCENT_HOVER = "#00e8bb"
    ACCENT_BLUE = "#0abde3"
    TEXT = "#e0e0e8"
    TEXT_DIM = "#8888a0"
    TEXT_MUTED = "#8888a0"
    BORDER = "#0f3460"
    BORDER_HOVER = "#1a5276"
    DANGER = "#e74c3c"
    WARNING = "#f39c12"
    SUCCESS = "#00b894"

    # Severity colors for table cells
    CRITICAL = "#e74c3c"
    HIGH = "#e67e22"
    MEDIUM = "#f39c12"
    LOW = "#27ae60"
    OK = "#00b894"
