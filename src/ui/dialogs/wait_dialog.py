"""
Wait dialog and busy cursor utilities for heavy data imports and UI refreshes.
"""
from __future__ import annotations
from typing import Optional
from PyQt6.QtWidgets import QProgressDialog, QApplication
from PyQt6.QtCore import Qt

from src.ui.styles import Colors


class BusyWaitDialog:
    """
    Context manager that displays a wait cursor (hourglass/reloj de espera)
    and an animated busy modal dialog while operations run.
    """
    def __init__(self, title: str, message: str, parent=None):
        self.title = title
        self.message = message
        self.parent = parent
        self.dialog: Optional[QProgressDialog] = None

    def __enter__(self):
        # 1. Set global wait cursor (reloj de espera)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        # 2. Build modal progress dialog
        self.dialog = QProgressDialog(self.message, None, 0, 0, self.parent)
        self.dialog.setWindowTitle(f"⏳ {self.title}")
        self.dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.dialog.setCancelButton(None)
        self.dialog.setMinimumDuration(0)
        self.dialog.setAutoClose(False)
        self.dialog.setAutoReset(False)
        self.dialog.setMinimumWidth(380)

        self.dialog.setStyleSheet(f"""
            QProgressDialog {{
                background-color: {Colors.BG_SECONDARY};
                color: {Colors.TEXT};
                border: 1px solid {Colors.ACCENT};
                border-radius: 8px;
                padding: 18px;
            }}
            QLabel {{
                color: #ffffff;
                font-size: 13px;
                font-weight: 600;
                padding-bottom: 8px;
            }}
            QProgressBar {{
                border: 1px solid {Colors.BORDER};
                border-radius: 4px;
                text-align: center;
                background-color: {Colors.BG_PRIMARY};
                height: 14px;
            }}
            QProgressBar::chunk {{
                background-color: {Colors.ACCENT};
            }}
        """)

        self.dialog.show()
        QApplication.processEvents()
        return self

    def set_text(self, text: str):
        """Update status message and process UI events."""
        if self.dialog:
            self.dialog.setLabelText(text)
            QApplication.processEvents()

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.dialog:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None

        # Restore cursor safely
        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()

        QApplication.processEvents()
