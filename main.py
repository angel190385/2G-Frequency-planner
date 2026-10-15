"""
Freq Planner 2G — Entry Point

GSM 2G frequency planning and optimization tool.
"""
import sys
import os

import multiprocessing

# Ensure the project root is in the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from src.ui.main_window import MainWindow
from src.ui.styles import DARK_THEME


def main():
    multiprocessing.freeze_support()
    # High DPI support
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    app = QApplication(sys.argv)
    app.setApplicationName("Freq Planner 2G")
    app.setOrganizationName("FreqPlanner")

    # Apply dark theme
    app.setStyleSheet(DARK_THEME)

    # Set default font
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    # Create and show main window maximized
    window = MainWindow()
    window.showMaximized()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
