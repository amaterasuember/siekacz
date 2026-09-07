from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QAbstractButton, QApplication, QWidget


DARK_QSS = """
QWidget {
    background: #07111f;
    color: #f8fafc;
    font-family: Segoe UI, Arial;
    font-size: 10pt;
}
QMainWindow, QDialog {
    background: #07111f;
}
QMenuBar {
    background: #07111f;
    color: #dbeafe;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    padding: 2px 8px;
}
QMenu {
    background: #111827;
    color: #f8fafc;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 10px;
    padding: 6px;
}
QMenu::item {
    padding: 8px 22px;
    border-radius: 7px;
}
QMenu::item:selected {
    background: #2563eb;
}
QWidget#appRoot {
    background: #07111f;
}
QWidget#appHeader {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0b1626, stop:1 #101826);
    border: 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.10);
}
QStackedWidget#mainStack {
    background: #07111f;
}
QStatusBar {
    background: #07111f;
    color: #93a4b8;
    border-top: 1px solid rgba(255, 255, 255, 0.08);
}
QPushButton {
    min-height: 30px;
    border-radius: 10px;
    padding: 6px 11px;
    font-weight: 650;
}
QPushButton#toolbarButton, QPushButton#smallButton, QPushButton#navTab {
    background: #111827;
    color: #f8fafc;
    border: 1px solid rgba(255, 255, 255, 0.10);
}
QPushButton#themeToggle {
    background: #111827;
    color: #f8fafc;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 13px;
    min-width: 32px;
    max-width: 32px;
    min-height: 26px;
    padding: 2px;
}
QPushButton#themeToggle:hover {
    background: #172233;
    border-color: rgba(90, 167, 255, 0.60);
}
QWidget#zoomControls {
    background: transparent;
}
QPushButton#zoomButton {
    background: #111827;
    color: #f8fafc;
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 12px;
    min-width: 34px;
    max-width: 34px;
    min-height: 30px;
    padding: 2px;
    font-size: 12pt;
    font-weight: 800;
}
QPushButton#zoomButton:hover {
    background: #172233;
    border-color: rgba(90, 167, 255, 0.65);
}
QPushButton#zoomButton:pressed {
    background: #0f1c2e;
    padding-top: 4px;
}
QPushButton#toolbarButton, QPushButton#navTab {
    min-height: 34px;
    padding-left: 14px;
    padding-right: 14px;
}
QPushButton#navTab {
    min-width: 92px;
}
QPushButton#toolbarButton:hover, QPushButton#smallButton:hover, QPushButton#navTab:hover {
    background: #172233;
    border-color: rgba(59, 130, 246, 0.60);
}
QPushButton#toolbarButton:pressed, QPushButton#smallButton:pressed, QPushButton#navTab:pressed {
    background: #0f1c2e;
    padding-top: 8px;
    padding-left: 13px;
}
QPushButton#navTab:checked, QPushButton#toolbarPrimary, QPushButton#primaryCalculate, QPushButton#primaryButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #5aa7ff, stop:1 #2563eb);
    color: #ffffff;
    border: 1px solid rgba(90, 167, 255, 0.70);
}
QPushButton#toolbarPrimary {
    min-height: 34px;
    padding-left: 18px;
    padding-right: 20px;
}
QPushButton#primaryCalculate {
    min-height: 42px;
    font-size: 11pt;
}
QPushButton#toolbarPrimary:hover, QPushButton#primaryCalculate:hover, QPushButton#primaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #78b7ff, stop:1 #2f80ff);
}
QPushButton#toolbarPrimary:pressed, QPushButton#primaryCalculate:pressed, QPushButton#primaryButton:pressed {
    background: #1d4ed8;
    padding-top: 8px;
    padding-left: 13px;
}
QPushButton:disabled {
    background: #101826;
    color: #64748b;
    border-color: rgba(255, 255, 255, 0.06);
}
QToolBar {
    background: #07111f;
    border: 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    spacing: 6px;
    padding: 6px;
}
QToolButton {
    background: #111827;
    color: #f8fafc;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 9px;
    padding: 7px 10px;
    font-weight: 650;
}
QToolButton:hover {
    background: #172233;
    border-color: rgba(59, 130, 246, 0.60);
}
QTabWidget::pane {
    background: #07111f;
    border: 1px solid rgba(148, 163, 184, 0.16);
    border-radius: 12px;
    top: -1px;
}
QTabBar::tab {
    background: #111827;
    color: #cbd5e1;
    border: 1px solid rgba(148, 163, 184, 0.14);
    border-bottom: 0;
    border-top-left-radius: 9px;
    border-top-right-radius: 9px;
    padding: 8px 14px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #2563eb;
    color: #ffffff;
}
QGroupBox, QGroupBox#glassPanel {
    background: #111827;
    color: #f8fafc;
    border: 1px solid rgba(148, 163, 184, 0.16);
    border-radius: 13px;
    margin-top: 12px;
    padding: 12px;
    font-weight: 750;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}
QWidget#workspaceRoot {
    background: #07111f;
}
QWidget#workspacePanel, QWidget#canvasStack {
    background: transparent;
}
QWidget#controlPanel, QWidget#sectionCard, QWidget#summaryPanel, QWidget#settingsCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #111827, stop:1 #0d1726);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 16px;
}
QWidget#controlPanel {
    border-color: rgba(148, 163, 184, 0.22);
}
QWidget#settingsCard {
    margin-top: 4px;
}
QLabel#sectionTitle, QLabel#settingsTitle {
    background: transparent;
    color: #f8fafc;
    font-size: 11pt;
    font-weight: 800;
}
QLabel#settingsTitle {
    font-size: 15pt;
}
QWidget#parameterRow {
    background: transparent;
}
QLabel#parameterIcon {
    background: #1f2937;
    color: #bfdbfe;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
    font-size: 11pt;
    font-weight: 700;
}
QLabel#parameterLabel {
    background: transparent;
    color: #e5edf7;
    font-weight: 550;
}
QLabel#compactLabel {
    background: transparent;
    color: #cbd5e1;
    font-size: 9pt;
    font-weight: 700;
}
QLabel#unitLabel {
    background: transparent;
    color: #b6c4d6;
    min-width: 34px;
}
QSpinBox#premiumInput, QDoubleSpinBox#premiumInput, QComboBox#premiumInput,
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: #0b1220;
    color: #f8fafc;
    border: 1px solid rgba(148, 163, 184, 0.22);
    border-radius: 9px;
    padding: 5px 9px;
    selection-background-color: #2563eb;
}
QSpinBox#premiumInput:focus, QDoubleSpinBox#premiumInput:focus, QComboBox#premiumInput:focus,
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid #3b82f6;
    background: #101826;
}
QComboBox::drop-down, QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    border: 0;
    width: 22px;
}
QTableWidget#partsTable, QTableView {
    background: #0a1322;
    alternate-background-color: #0d1829;
    color: #f8fafc;
    gridline-color: rgba(148, 163, 184, 0.14);
    selection-background-color: rgba(37, 99, 235, 0.72);
    selection-color: white;
    border: 1px solid rgba(148, 163, 184, 0.16);
    border-radius: 12px;
    outline: 0;
}
QTableWidget#partsTable::item {
    padding: 5px 8px;
    border-bottom: 1px solid rgba(148, 163, 184, 0.10);
}
QTableWidget#partsTable::item:hover {
    background: rgba(59, 130, 246, 0.12);
}
QLineEdit#tableEditor {
    background: #0f1c2e;
    color: #ffffff;
    border: 1px solid #3b82f6;
    border-radius: 6px;
    padding: 2px 7px;
    min-height: 24px;
    selection-background-color: #2563eb;
}
QHeaderView::section {
    background: #111c2d;
    color: #dbeafe;
    padding: 7px;
    border: 0;
    border-right: 1px solid rgba(148, 163, 184, 0.14);
    font-weight: 800;
}
QWidget#metricCard {
    background: #121d2e;
    border: 1px solid rgba(148, 163, 184, 0.16);
    border-radius: 13px;
}
QLabel#metricIcon {
    background: transparent;
    color: #3b82f6;
    font-size: 11pt;
    font-weight: 800;
}
QLabel#metricLabel {
    background: transparent;
    color: #b6c4d6;
    font-size: 8pt;
}
QLabel#metricValue {
    background: transparent;
    color: #f8fafc;
    font-size: 11pt;
    font-weight: 850;
}
QWidget#metricCard[metricAccent="success"] QLabel#metricValue,
QWidget#metricCard[metricAccent="success"] QLabel#metricIcon {
    color: #4ade80;
}
QWidget#metricCard[metricAccent="danger"] QLabel#metricValue,
QWidget#metricCard[metricAccent="danger"] QLabel#metricIcon {
    color: #fb7185;
}
QWidget#metricCard[metricAccent="green"] QLabel#metricValue,
QWidget#metricCard[metricAccent="green"] QLabel#metricIcon {
    color: #34d399;
}
QLabel#summaryLabel {
    background: rgba(15, 23, 42, 0.88);
    border: 1px solid rgba(148, 163, 184, 0.16);
    border-radius: 12px;
    padding: 7px 11px;
    color: #b6c4d6;
}
QLabel#warningBanner {
    background: rgba(239, 68, 68, 0.14);
    border: 1px solid rgba(239, 68, 68, 0.55);
    border-radius: 12px;
    padding: 9px 13px;
    color: #fecdd3;
    font-weight: 800;
}
QSplitter::handle {
    background: transparent;
    width: 14px;
}
QScrollArea#controlScroll {
    background: transparent;
    border: 0;
}
QScrollArea#controlScroll > QWidget > QWidget {
    background: transparent;
}
QGraphicsView#layoutView {
    border: 1px solid rgba(148, 163, 184, 0.16);
    border-radius: 16px;
    background: #07111f;
}
QScrollBar:vertical, QScrollBar:horizontal {
    background: #07111f;
    border: 0;
    width: 12px;
    height: 12px;
}
QScrollBar::handle {
    background: rgba(148, 163, 184, 0.42);
    border-radius: 6px;
}
QScrollBar::handle:hover {
    background: rgba(148, 163, 184, 0.62);
}
"""

PREMIUM_GLASS_QSS = """
QWidget {
    font-family: Inter, "SF Pro Display", "Segoe UI", Arial;
}
QWidget#appRoot, QWidget#workspaceRoot {
    background: qradialgradient(cx:0.72, cy:0.08, radius:1.05, stop:0 rgba(50, 98, 160, 0.28), stop:0.36 #07101f, stop:1 #050b18);
}
QWidget#appHeader {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(255,255,255,0.105), stop:1 rgba(255,255,255,0.035));
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 18px;
}
QWidget#brandBlock {
    background: transparent;
}
QLabel#brandIcon {
    min-width: 40px;
    max-width: 40px;
    min-height: 40px;
    max-height: 40px;
    border-radius: 0;
    color: #eaf2ff;
    font-size: 14pt;
    font-weight: 900;
    background: transparent;
    border: none;
}
QLabel#brandTitle {
    background: transparent;
    color: rgba(255,255,255,0.94);
    font-size: 13pt;
    font-weight: 850;
}
QLabel#brandSubtitle {
    background: transparent;
    color: rgba(255,255,255,0.58);
    font-size: 9pt;
}
QWidget#controlPanel, QWidget#sectionCard, QWidget#settingsCard, QWidget#workspacePanel, QWidget#canvasStack {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255,255,255,0.075), stop:1 rgba(255,255,255,0.035));
    border: 1px solid rgba(255, 255, 255, 0.11);
    border-radius: 22px;
}
QWidget#controlPanel {
    border: 0;
    background: transparent;
}
QWidget#workspacePanel {
    border: 0;
    background: transparent;
}
QWidget#workspacePanel {
    padding: 0;
}
QLabel#sectionTitle, QLabel#settingsTitle {
    color: rgba(255,255,255,0.92);
    font-weight: 760;
}
QLabel#parameterIcon {
    background: rgba(255,255,255,0.075);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 10px;
    color: #b9d8ff;
}
QLabel#parameterLabel {
    color: rgba(255,255,255,0.82);
    font-weight: 520;
}
QLabel#compactLabel {
    color: rgba(255,255,255,0.66);
    font-size: 9pt;
    font-weight: 700;
}
QLabel#unitLabel {
    color: rgba(255,255,255,0.58);
}
QPushButton {
    border-radius: 14px;
    min-height: 34px;
    font-weight: 650;
}
QPushButton#toolbarButton, QPushButton#smallButton, QPushButton#navTab {
    background: rgba(255,255,255,0.055);
    border: 1px solid rgba(255,255,255,0.10);
    color: rgba(255,255,255,0.88);
}
QPushButton#toolbarButton:hover, QPushButton#smallButton:hover, QPushButton#navTab:hover {
    background: rgba(255,255,255,0.10);
    border-color: rgba(140,190,255,0.38);
}
QPushButton#navTab:checked, QPushButton#toolbarPrimary, QPushButton#primaryCalculate, QPushButton#primaryButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #5aa7ff, stop:1 #2563eb);
    color: white;
    border: 1px solid rgba(140,190,255,0.46);
}
QPushButton#primaryCalculate {
    min-height: 48px;
    font-size: 13pt;
    border-radius: 16px;
}
QPushButton:pressed {
    padding-top: 7px;
    padding-bottom: 5px;
}
QSpinBox#premiumInput, QDoubleSpinBox#premiumInput, QComboBox#premiumInput,
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: rgba(4, 12, 25, 0.46);
    color: rgba(255,255,255,0.92);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 13px;
    min-height: 34px;
    padding: 5px 12px;
}
QSpinBox#premiumInput:focus, QDoubleSpinBox#premiumInput:focus, QComboBox#premiumInput:focus,
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid rgba(90,167,255,0.72);
    background: rgba(7, 18, 36, 0.72);
}
QTableWidget#partsTable {
    background: transparent;
    border: 0;
    gridline-color: transparent;
    alternate-background-color: rgba(255,255,255,0.028);
    selection-background-color: rgba(90,167,255,0.28);
    selection-color: white;
}
QTableWidget#partsTable::item {
    border: 0;
    padding: 8px;
}
QHeaderView::section {
    background: rgba(255,255,255,0.045);
    color: rgba(255,255,255,0.68);
    border: 0;
    border-bottom: 1px solid rgba(255,255,255,0.08);
    padding: 8px;
    font-weight: 620;
}
QLabel#warningBanner {
    background: rgba(255, 111, 145, 0.13);
    border: 1px solid rgba(255, 111, 145, 0.38);
    border-radius: 16px;
    padding: 10px 14px;
    color: #ffd4de;
}
QGraphicsView#layoutView {
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 20px;
    background: transparent;
}
QStatusBar {
    background: rgba(255,255,255,0.055);
    color: rgba(255,255,255,0.68);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
}
QScrollBar:vertical, QScrollBar:horizontal {
    background: transparent;
    border: 0;
    width: 12px;
    height: 12px;
}
QScrollBar::handle {
    background: rgba(255,255,255,0.18);
    border-radius: 6px;
}
QScrollBar::handle:hover {
    background: rgba(255,255,255,0.30);
}
"""


LIQUID_DARK_QSS = """
/* Premium liquid-glass dark skin. UI-only override; no geometry or optimizer logic. */
QWidget {
    font-family: Inter, "SF Pro Display", "Segoe UI", Arial;
    selection-background-color: rgba(90, 167, 255, 0.34);
    selection-color: #ffffff;
}
QMainWindow, QDialog, QWidget#appRoot, QWidget#workspaceRoot, QStackedWidget#mainStack {
    background: qradialgradient(cx:0.18, cy:0.04, radius:1.1,
                stop:0 rgba(80, 120, 255, 0.17),
                stop:0.30 rgba(7, 12, 22, 0.98),
                stop:1 #05080d);
    color: rgba(248, 250, 252, 0.94);
}
QWidget#appRoot {
    border: 0;
}
QWidget#appHeader {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(255,255,255,0.145),
                stop:0.46 rgba(255,255,255,0.070),
                stop:1 rgba(76,201,255,0.035));
    border: 1px solid rgba(255,255,255,0.145);
    border-radius: 24px;
}
QWidget#appHeader:hover {
    border-color: rgba(155, 205, 255, 0.24);
}
QLabel#brandIcon {
    background: transparent;
    border: none;
    border-radius: 0;
}
QLabel#brandTitle {
    color: rgba(255,255,255,0.96);
    font-size: 13pt;
    font-weight: 900;
    letter-spacing: 0px;
}
QLabel#brandSubtitle {
    color: rgba(226, 239, 255, 0.58);
    font-size: 8.5pt;
}
QWidget#sectionCard, QWidget#settingsCard {
    background: qlineargradient(x1:0, y1:0, x2:0.8, y2:1,
                stop:0 rgba(255,255,255,0.092),
                stop:0.42 rgba(255,255,255,0.050),
                stop:1 rgba(255,255,255,0.030));
    border: 1px solid rgba(255,255,255,0.135);
    border-radius: 24px;
}
QWidget#sectionCard:hover, QWidget#settingsCard:hover {
    border-color: rgba(150, 205, 255, 0.24);
    background: qlineargradient(x1:0, y1:0, x2:0.8, y2:1,
                stop:0 rgba(255,255,255,0.105),
                stop:0.46 rgba(255,255,255,0.058),
                stop:1 rgba(255,255,255,0.034));
}
QWidget#canvasStack {
    background: qradialgradient(cx:0.50, cy:0.18, radius:0.92,
                stop:0 rgba(90, 167, 255, 0.095),
                stop:0.46 rgba(255,255,255,0.030),
                stop:1 rgba(255,255,255,0.012));
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 24px;
}
QWidget#workspacePanel {
    background: transparent;
    border: 0;
}
QGraphicsView#layoutView {
    background: transparent;
    border: 1px solid rgba(190, 220, 255, 0.145);
    border-radius: 22px;
}
QLabel#sectionTitle, QLabel#settingsTitle {
    color: rgba(255,255,255,0.94);
    font-weight: 820;
}
QLabel#compactLabel, QLabel#summaryLabel {
    color: rgba(220, 234, 252, 0.70);
}
QLabel#parameterIcon {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(255,255,255,0.115),
                stop:1 rgba(255,255,255,0.045));
    border: 1px solid rgba(255,255,255,0.13);
    border-radius: 11px;
    color: #cfe6ff;
}
QLabel#parameterLabel {
    color: rgba(255,255,255,0.84);
    font-weight: 580;
}
QLabel#unitLabel {
    color: rgba(226, 239, 255, 0.60);
}
QPushButton {
    border-radius: 15px;
    min-height: 34px;
    padding: 7px 13px;
    font-weight: 700;
}
QPushButton#toolbarButton, QPushButton#smallButton, QPushButton#navTab, QPushButton#zoomButton {
    background: qlineargradient(x1:0, y1:0, x2:0.85, y2:1,
                stop:0 rgba(255,255,255,0.105),
                stop:1 rgba(255,255,255,0.040));
    color: rgba(248,250,252,0.91);
    border: 1px solid rgba(255,255,255,0.12);
}
QPushButton#toolbarButton:hover, QPushButton#smallButton:hover, QPushButton#navTab:hover, QPushButton#zoomButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0.85, y2:1,
                stop:0 rgba(255,255,255,0.150),
                stop:1 rgba(90,167,255,0.070));
    border-color: rgba(150,205,255,0.38);
    color: #ffffff;
}
QPushButton#toolbarButton:pressed, QPushButton#smallButton:pressed, QPushButton#navTab:pressed, QPushButton#zoomButton:pressed {
    background: rgba(255,255,255,0.075);
    padding-top: 8px;
}
QPushButton#navTab:checked, QPushButton#toolbarPrimary, QPushButton#primaryCalculate, QPushButton#primaryButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #72bbff,
                stop:0.46 #2f80ff,
                stop:1 #1d4ed8);
    color: #ffffff;
    border: 1px solid rgba(180, 220, 255, 0.54);
}
QPushButton#navTab:checked:hover, QPushButton#toolbarPrimary:hover, QPushButton#primaryCalculate:hover, QPushButton#primaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #8dccff,
                stop:0.48 #4aa3ff,
                stop:1 #2563eb);
    border-color: rgba(210, 235, 255, 0.68);
}
QPushButton#primaryCalculate {
    min-height: 50px;
    font-size: 13pt;
    border-radius: 18px;
}
QPushButton:disabled {
    background: rgba(255,255,255,0.035);
    color: rgba(148, 163, 184, 0.52);
    border-color: rgba(255,255,255,0.055);
}
QSpinBox#premiumInput, QDoubleSpinBox#premiumInput, QComboBox#premiumInput,
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: rgba(5, 12, 24, 0.58);
    color: rgba(255,255,255,0.94);
    border: 1px solid rgba(255,255,255,0.115);
    border-radius: 15px;
    min-height: 36px;
    padding: 6px 12px;
}
QSpinBox#premiumInput:hover, QDoubleSpinBox#premiumInput:hover, QComboBox#premiumInput:hover,
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {
    border-color: rgba(150,205,255,0.28);
    background: rgba(7, 18, 36, 0.66);
}
QSpinBox#premiumInput:focus, QDoubleSpinBox#premiumInput:focus, QComboBox#premiumInput:focus,
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid rgba(90, 167, 255, 0.82);
    background: rgba(8, 22, 44, 0.84);
}
QComboBox QAbstractItemView {
    background: rgba(8, 18, 34, 0.98);
    color: rgba(255,255,255,0.92);
    border: 1px solid rgba(150,205,255,0.25);
    border-radius: 14px;
    selection-background-color: rgba(90,167,255,0.38);
    padding: 6px;
}
QTableWidget, QTableView {
    background: rgba(4, 12, 25, 0.20);
    border: 0;
    gridline-color: rgba(255,255,255,0.055);
    alternate-background-color: rgba(255,255,255,0.025);
    selection-background-color: rgba(90,167,255,0.30);
    selection-color: #ffffff;
}
QTableWidget::item, QTableView::item {
    border: 0;
    padding: 8px;
}
QTableWidget::item:hover, QTableView::item:hover {
    background: rgba(255,255,255,0.045);
}
QHeaderView::section {
    background: rgba(255,255,255,0.055);
    color: rgba(226,239,255,0.72);
    border: 0;
    border-bottom: 1px solid rgba(255,255,255,0.085);
    padding: 8px;
    font-weight: 720;
}
QLabel#warningBanner {
    background: rgba(255, 111, 145, 0.145);
    border: 1px solid rgba(255, 111, 145, 0.42);
    border-radius: 18px;
    padding: 10px 14px;
    color: #ffd8e2;
}
QScrollArea, QScrollArea#controlScroll, QScrollArea#controlScroll > QWidget > QWidget {
    background: transparent;
    border: 0;
}
QSplitter::handle {
    background: rgba(255,255,255,0.030);
    border-radius: 6px;
}
QStatusBar {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(255,255,255,0.080),
                stop:1 rgba(255,255,255,0.035));
    color: rgba(220,234,252,0.70);
    border: 1px solid rgba(255,255,255,0.105);
    border-radius: 18px;
}
QPushButton#themeToggle {
    background: rgba(255,255,255,0.075);
    border: 1px solid rgba(255,255,255,0.13);
    border-radius: 14px;
}
QPushButton#themeToggle:hover {
    background: rgba(255,255,255,0.13);
    border-color: rgba(150,205,255,0.38);
}
QScrollBar:vertical, QScrollBar:horizontal {
    background: transparent;
    border: 0;
    width: 12px;
    height: 12px;
}
QScrollBar::handle {
    background: rgba(255,255,255,0.20);
    border-radius: 6px;
}
QScrollBar::handle:hover {
    background: rgba(255,255,255,0.34);
}
/* ── Vista liquid-glass: per-element overrides ───────────────────────────── */
/* Glass panel top-shine: bright highlight at very top, fade to translucent */
QWidget#sectionCard, QWidget#settingsCard {
    background: qlineargradient(x1:0, y1:0, x2:0.55, y2:1,
                stop:0.00 rgba(255,255,255,0.200),
                stop:0.09 rgba(255,255,255,0.092),
                stop:0.44 rgba(255,255,255,0.048),
                stop:1.00 rgba(255,255,255,0.020));
    border: 1px solid rgba(255,255,255,0.195);
    border-top-color: rgba(255,255,255,0.400);
    border-radius: 24px;
}
/* Vista gel zoom buttons — convex "bubble" highlight at top third */
QPushButton#zoomButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0.00 rgba(255,255,255,0.56),
                stop:0.39 rgba(255,255,255,0.16),
                stop:0.41 rgba(255,255,255,0.06),
                stop:1.00 rgba(255,255,255,0.11));
    border: 1px solid rgba(255,255,255,0.28);
    border-top-color: rgba(255,255,255,0.65);
    border-bottom-color: rgba(0,0,0,0.32);
    border-radius: 18px;
    color: rgba(228,244,255,0.97);
    min-width: 36px;
    max-width: 36px;
    min-height: 36px;
    max-height: 36px;
    padding: 0px;
    font-size: 14pt;
    font-weight: 900;
}
QPushButton#zoomButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0.00 rgba(210,238,255,0.74),
                stop:0.41 rgba(105,178,255,0.30),
                stop:1.00 rgba(60,122,245,0.24));
    border-color: rgba(162,218,255,0.58);
    border-top-color: rgba(238,252,255,0.88);
    border-bottom-color: rgba(30,80,200,0.28);
    color: white;
}
QPushButton#zoomButton:pressed {
    background: rgba(255,255,255,0.09);
    border-color: rgba(100,165,242,0.45);
    border-top-color: rgba(255,255,255,0.22);
    padding-top: 4px;
}
/* Cut-history list embedded in animation panel */
QListWidget#cutHistoryList {
    background: rgba(2, 7, 16, 0.92);
    border: none;
    border-top: 1px solid rgba(255,255,255,0.09);
    border-radius: 0 0 12px 12px;
    color: rgba(178,212,255,0.84);
    font-size: 8pt;
    outline: 0;
}
QListWidget#cutHistoryList::item {
    padding: 3px 9px;
    border-bottom: 1px solid rgba(255,255,255,0.035);
}
QListWidget#cutHistoryList::item:selected {
    background: rgba(48,118,255,0.44);
    color: rgba(218,240,255,1.0);
    border-bottom-color: rgba(48,118,255,0.22);
}
QListWidget#cutHistoryList::item:hover:!selected {
    background: rgba(255,255,255,0.052);
}
"""


LIGHT_QSS = """
QWidget {
    background: #f3f7fb;
    color: #102033;
    font-family: Segoe UI, Arial;
    font-size: 10pt;
}
QMainWindow, QDialog {
    background: #f3f7fb;
}
QMenuBar {
    background: #ffffff;
    color: #1f2f46;
    border-bottom: 1px solid rgba(15, 23, 42, 0.10);
    padding: 2px 8px;
}
QMenu {
    background: #ffffff;
    color: #102033;
    border: 1px solid rgba(15, 23, 42, 0.12);
    border-radius: 10px;
    padding: 6px;
}
QMenu::item {
    padding: 8px 22px;
    border-radius: 7px;
}
QMenu::item:selected {
    background: #2563eb;
    color: #ffffff;
}
QWidget#appRoot {
    background: #f3f7fb;
}
QWidget#appHeader {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ffffff, stop:1 #eef5ff);
    border: 0;
    border-bottom: 1px solid rgba(15, 23, 42, 0.10);
}
QStackedWidget#mainStack {
    background: #f3f7fb;
}
QStatusBar {
    background: #ffffff;
    color: #617089;
    border-top: 1px solid rgba(15, 23, 42, 0.10);
}
QPushButton {
    min-height: 30px;
    border-radius: 10px;
    padding: 6px 11px;
    font-weight: 650;
}
QPushButton#toolbarButton, QPushButton#smallButton, QPushButton#navTab {
    background: #ffffff;
    color: #102033;
    border: 1px solid rgba(15, 23, 42, 0.12);
}
QPushButton#toolbarButton, QPushButton#navTab {
    min-height: 34px;
    padding-left: 14px;
    padding-right: 14px;
}
QPushButton#navTab {
    min-width: 92px;
}
QPushButton#toolbarButton:hover, QPushButton#smallButton:hover, QPushButton#navTab:hover {
    background: #eef5ff;
    border-color: rgba(37, 99, 235, 0.55);
}
QPushButton#toolbarButton:pressed, QPushButton#smallButton:pressed, QPushButton#navTab:pressed {
    background: #dfeeff;
}
QWidget#zoomControls {
    background: transparent;
}
QPushButton#zoomButton {
    background: #ffffff;
    color: #1f2937;
    border: 1px solid rgba(15, 23, 42, 0.14);
    border-radius: 12px;
    min-width: 34px;
    max-width: 34px;
    min-height: 30px;
    padding: 2px;
    font-size: 12pt;
    font-weight: 800;
}
QPushButton#zoomButton:hover {
    background: #eef5ff;
    border-color: rgba(47,128,255,0.45);
}
QPushButton#zoomButton:pressed {
    background: #dfeeff;
    padding-top: 4px;
}
QPushButton#navTab:checked, QPushButton#toolbarPrimary, QPushButton#primaryCalculate, QPushButton#primaryButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #5aa7ff, stop:1 #2563eb);
    color: #ffffff;
    border: 1px solid rgba(47, 128, 255, 0.48);
}
QPushButton#toolbarPrimary {
    min-height: 34px;
    padding-left: 18px;
    padding-right: 20px;
}
QPushButton#primaryCalculate {
    min-height: 42px;
    font-size: 11pt;
}
QPushButton#toolbarPrimary:hover, QPushButton#primaryCalculate:hover, QPushButton#primaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #78b7ff, stop:1 #2f80ff);
}
QPushButton#toolbarPrimary:pressed, QPushButton#primaryCalculate:pressed, QPushButton#primaryButton:pressed {
    background: #1d4ed8;
}
QPushButton:disabled {
    background: #e8eef6;
    color: #95a3b8;
    border-color: rgba(15, 23, 42, 0.08);
}
QToolBar {
    background: #ffffff;
    border: 0;
    border-bottom: 1px solid rgba(15, 23, 42, 0.10);
    spacing: 6px;
    padding: 6px;
}
QToolButton {
    background: #ffffff;
    color: #102033;
    border: 1px solid rgba(15, 23, 42, 0.12);
    border-radius: 9px;
    padding: 7px 10px;
    font-weight: 650;
}
QToolButton:hover {
    background: #eef5ff;
    border-color: rgba(37, 99, 235, 0.55);
}
QTabWidget::pane {
    background: #f3f7fb;
    border: 1px solid rgba(15, 23, 42, 0.10);
    border-radius: 12px;
    top: -1px;
}
QTabBar::tab {
    background: #ffffff;
    color: #53647d;
    border: 1px solid rgba(15, 23, 42, 0.10);
    border-bottom: 0;
    border-top-left-radius: 9px;
    border-top-right-radius: 9px;
    padding: 8px 14px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #2563eb;
    color: #ffffff;
}
QGroupBox, QGroupBox#glassPanel {
    background: #ffffff;
    color: #102033;
    border: 1px solid rgba(15, 23, 42, 0.10);
    border-radius: 13px;
    margin-top: 12px;
    padding: 12px;
    font-weight: 750;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}
QWidget#workspaceRoot {
    background: #f3f7fb;
}
QWidget#workspacePanel, QWidget#canvasStack {
    background: transparent;
}
QWidget#controlPanel, QWidget#sectionCard, QWidget#summaryPanel, QWidget#settingsCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #f7fbff);
    border: 1px solid rgba(15, 23, 42, 0.10);
    border-radius: 16px;
}
QWidget#controlPanel {
    border-color: rgba(15, 23, 42, 0.14);
}
QWidget#settingsCard {
    margin-top: 4px;
}
QLabel#sectionTitle, QLabel#settingsTitle {
    background: transparent;
    color: #102033;
    font-size: 11pt;
    font-weight: 800;
}
QLabel#settingsTitle {
    font-size: 15pt;
}
QWidget#parameterRow {
    background: transparent;
}
QLabel#parameterIcon {
    background: #eef5ff;
    color: #2563eb;
    border: 1px solid rgba(37, 99, 235, 0.16);
    border-radius: 8px;
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
    font-size: 11pt;
    font-weight: 700;
}
QLabel#parameterLabel {
    background: transparent;
    color: #203047;
    font-weight: 600;
}
QLabel#compactLabel {
    background: transparent;
    color: #53647d;
    font-size: 9pt;
    font-weight: 700;
}
QLabel#unitLabel {
    background: transparent;
    color: #53647d;
    min-width: 34px;
}
QSpinBox#premiumInput, QDoubleSpinBox#premiumInput, QComboBox#premiumInput,
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: #ffffff;
    color: #102033;
    border: 1px solid rgba(15, 23, 42, 0.14);
    border-radius: 9px;
    padding: 5px 9px;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}
QSpinBox#premiumInput:focus, QDoubleSpinBox#premiumInput:focus, QComboBox#premiumInput:focus,
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid #3b82f6;
    background: #fbfdff;
}
QComboBox::drop-down, QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    border: 0;
    width: 22px;
}
QTableWidget#partsTable, QTableView {
    background: #ffffff;
    alternate-background-color: #f6f9fd;
    color: #102033;
    gridline-color: rgba(15, 23, 42, 0.08);
    selection-background-color: rgba(37, 99, 235, 0.18);
    selection-color: #102033;
    border: 1px solid rgba(15, 23, 42, 0.10);
    border-radius: 12px;
    outline: 0;
}
QTableWidget#partsTable::item {
    padding: 5px 8px;
    border-bottom: 1px solid rgba(15, 23, 42, 0.07);
}
QTableWidget#partsTable::item:hover {
    background: rgba(59, 130, 246, 0.08);
}
QLineEdit#tableEditor {
    background: #ffffff;
    color: #102033;
    border: 1px solid #3b82f6;
    border-radius: 6px;
    padding: 2px 7px;
    min-height: 24px;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}
QHeaderView::section {
    background: #eaf2ff;
    color: #1f2f46;
    padding: 7px;
    border: 0;
    border-right: 1px solid rgba(15, 23, 42, 0.08);
    font-weight: 800;
}
QWidget#metricCard {
    background: #ffffff;
    border: 1px solid rgba(15, 23, 42, 0.10);
    border-radius: 13px;
}
QLabel#metricIcon {
    background: transparent;
    color: #2563eb;
    font-size: 11pt;
    font-weight: 800;
}
QLabel#metricLabel {
    background: transparent;
    color: #53647d;
    font-size: 8pt;
}
QLabel#metricValue {
    background: transparent;
    color: #102033;
    font-size: 11pt;
    font-weight: 850;
}
QWidget#metricCard[metricAccent="success"] QLabel#metricValue,
QWidget#metricCard[metricAccent="success"] QLabel#metricIcon {
    color: #16a34a;
}
QWidget#metricCard[metricAccent="danger"] QLabel#metricValue,
QWidget#metricCard[metricAccent="danger"] QLabel#metricIcon {
    color: #dc2626;
}
QWidget#metricCard[metricAccent="green"] QLabel#metricValue,
QWidget#metricCard[metricAccent="green"] QLabel#metricIcon {
    color: #059669;
}
QLabel#summaryLabel {
    background: #f8fbff;
    border: 1px solid rgba(15, 23, 42, 0.10);
    border-radius: 12px;
    padding: 7px 11px;
    color: #53647d;
}
QLabel#warningBanner {
    background: #fff1f2;
    border: 1px solid rgba(220, 38, 38, 0.34);
    border-radius: 12px;
    padding: 9px 13px;
    color: #b91c1c;
    font-weight: 800;
}
QSplitter::handle {
    background: transparent;
    width: 14px;
}
QScrollArea#controlScroll {
    background: transparent;
    border: 0;
}
QScrollArea#controlScroll > QWidget > QWidget {
    background: transparent;
}
QGraphicsView#layoutView {
    border: 1px solid rgba(15, 23, 42, 0.10);
    border-radius: 16px;
    background: #f3f7fb;
}
QScrollBar:vertical, QScrollBar:horizontal {
    background: #f3f7fb;
    border: 0;
    width: 12px;
    height: 12px;
}
QScrollBar::handle {
    background: rgba(100, 116, 139, 0.34);
    border-radius: 6px;
}
QScrollBar::handle:hover {
    background: rgba(100, 116, 139, 0.54);
}
"""


LIGHT_PREMIUM_GLASS_QSS = """
QWidget {
    font-family: Inter, "SF Pro Display", "Segoe UI", Arial;
}
QWidget#appRoot, QWidget#workspaceRoot {
    background: qradialgradient(cx:0.74, cy:0.04, radius:1.10, stop:0 rgba(212, 231, 255, 0.72), stop:0.36 #f7f9fc, stop:1 #eef2f7);
    color: #1f2937;
}
QWidget#appHeader {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(255,255,255,0.96), stop:1 rgba(246,249,253,0.90));
    border: 1px solid rgba(15, 23, 42, 0.10);
    border-radius: 18px;
}
QWidget#brandBlock {
    background: transparent;
}
QLabel#brandIcon {
    min-width: 40px;
    max-width: 40px;
    min-height: 40px;
    max-height: 40px;
    border-radius: 0;
    color: #2f80ff;
    background: transparent;
    border: none;
}
QLabel#brandTitle {
    background: transparent;
    color: #1f2937;
    font-size: 13pt;
    font-weight: 850;
}
QLabel#brandSubtitle {
    background: transparent;
    color: #667085;
    font-size: 9pt;
}
QWidget#controlPanel, QWidget#workspacePanel {
    border: 0;
    background: transparent;
}
QWidget#sectionCard, QWidget#settingsCard, QWidget#canvasStack {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255,255,255,0.98), stop:1 rgba(250,252,255,0.94));
    border: 1px solid rgba(15, 23, 42, 0.10);
    border-radius: 22px;
}
QLabel#sectionTitle, QLabel#settingsTitle {
    background: transparent;
    color: #1f2937;
    font-weight: 760;
}
QWidget#parameterRow {
    background: transparent;
}
QLabel#parameterIcon {
    background: #eef5ff;
    border: 1px solid rgba(47,128,255,0.14);
    border-radius: 10px;
    color: #2f80ff;
}
QLabel#parameterLabel {
    background: transparent;
    color: #334155;
    font-weight: 560;
}
QLabel#compactLabel {
    background: transparent;
    color: #64748b;
    font-size: 9pt;
    font-weight: 700;
}
QLabel#unitLabel {
    background: transparent;
    color: #667085;
}
QPushButton {
    border-radius: 14px;
    min-height: 34px;
    font-weight: 650;
}
QPushButton#toolbarButton, QPushButton#smallButton, QPushButton#navTab {
    background: rgba(255,255,255,0.82);
    border: 1px solid rgba(15, 23, 42, 0.10);
    color: #1f2937;
}
QPushButton#themeToggle {
    background: rgba(255,255,255,0.88);
    color: #1f2937;
    border: 1px solid rgba(15, 23, 42, 0.12);
    border-radius: 13px;
    min-width: 32px;
    max-width: 32px;
    min-height: 26px;
    padding: 2px;
}
QPushButton#themeToggle:hover {
    background: #eef5ff;
    border-color: rgba(47,128,255,0.36);
}
QPushButton#toolbarButton:hover, QPushButton#smallButton:hover, QPushButton#navTab:hover {
    background: #f1f6ff;
    border-color: rgba(47,128,255,0.38);
    color: #0f172a;
}
QPushButton#toolbarButton:pressed, QPushButton#smallButton:pressed, QPushButton#navTab:pressed {
    background: #e6f0ff;
    padding-top: 8px;
    padding-left: 13px;
}
QPushButton#navTab:checked, QPushButton#toolbarPrimary, QPushButton#primaryCalculate, QPushButton#primaryButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #5aa7ff, stop:1 #2563eb);
    color: #ffffff;
    border: 1px solid rgba(47,128,255,0.42);
}
QPushButton#primaryCalculate {
    min-height: 48px;
    font-size: 13pt;
    border-radius: 16px;
}
QPushButton#toolbarPrimary:hover, QPushButton#primaryCalculate:hover, QPushButton#primaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #78b7ff, stop:1 #2f80ff);
}
QPushButton#toolbarPrimary:pressed, QPushButton#primaryCalculate:pressed, QPushButton#primaryButton:pressed {
    background: #1d4ed8;
    padding-top: 8px;
    padding-left: 13px;
}
QPushButton:disabled {
    background: #e9eef5;
    color: #98a2b3;
    border: 1px solid rgba(15, 23, 42, 0.07);
}
QSpinBox#premiumInput, QDoubleSpinBox#premiumInput, QComboBox#premiumInput,
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: #ffffff;
    color: #1f2937;
    border: 1px solid rgba(15, 23, 42, 0.14);
    border-radius: 13px;
    min-height: 34px;
    padding: 5px 12px;
    selection-background-color: #2f80ff;
    selection-color: #ffffff;
}
QSpinBox#premiumInput:focus, QDoubleSpinBox#premiumInput:focus, QComboBox#premiumInput:focus,
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid #2f80ff;
    background: #fcfdff;
}
QComboBox::drop-down, QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    border: 0;
    width: 22px;
}
QTableWidget#partsTable {
    background: #ffffff;
    border: 1px solid rgba(15, 23, 42, 0.09);
    border-radius: 14px;
    gridline-color: transparent;
    alternate-background-color: #f8fafc;
    color: #1f2937;
    selection-background-color: rgba(47,128,255,0.16);
    selection-color: #111827;
}
QTableWidget#partsTable::item {
    border: 0;
    border-bottom: 1px solid rgba(15, 23, 42, 0.06);
    padding: 8px;
}
QTableWidget#partsTable::item:hover {
    background: rgba(47,128,255,0.08);
}
QLineEdit#tableEditor {
    background: #ffffff;
    color: #111827;
    border: 1px solid #2f80ff;
    border-radius: 8px;
    padding: 2px 7px;
    min-height: 24px;
    selection-background-color: #2f80ff;
    selection-color: #ffffff;
}
QHeaderView::section {
    background: #f3f6fb;
    color: #475467;
    border: 0;
    border-bottom: 1px solid rgba(15, 23, 42, 0.07);
    padding: 8px;
    font-weight: 720;
}
QLabel#summaryLabel {
    background: #f8fafc;
    border: 1px solid rgba(15, 23, 42, 0.08);
    border-radius: 12px;
    padding: 8px 11px;
    color: #667085;
}
QLabel#warningBanner {
    background: #fff7ed;
    border: 1px solid rgba(251, 146, 60, 0.38);
    border-radius: 16px;
    padding: 10px 14px;
    color: #9a3412;
}
QGraphicsView#layoutView {
    border: 1px solid rgba(15, 23, 42, 0.10);
    border-radius: 20px;
    background: #f8fafc;
}
QStatusBar {
    background: rgba(255,255,255,0.84);
    color: #667085;
    border: 1px solid rgba(15, 23, 42, 0.08);
    border-radius: 16px;
}
QScrollArea#controlScroll,
QScrollArea#controlScroll > QWidget > QWidget {
    background: transparent;
    border: 0;
}
QSplitter::handle {
    background: transparent;
    width: 14px;
}
QScrollBar:vertical, QScrollBar:horizontal {
    background: transparent;
    border: 0;
    width: 12px;
    height: 12px;
}
QScrollBar::handle {
    background: rgba(100,116,139,0.30);
    border-radius: 6px;
}
QScrollBar::handle:hover {
    background: rgba(100,116,139,0.48);
}
"""


SPORT_ACCENT_QSS = """
QPushButton#navTab:checked, QPushButton#toolbarPrimary, QPushButton#primaryCalculate, QPushButton#primaryButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #fb7185, stop:1 #dc2626);
    color: #ffffff;
    border: 1px solid rgba(248, 113, 113, 0.58);
}
QPushButton#toolbarPrimary:hover, QPushButton#primaryCalculate:hover, QPushButton#primaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ff8a9a, stop:1 #ef4444);
}
QPushButton#toolbarPrimary:pressed, QPushButton#primaryCalculate:pressed, QPushButton#primaryButton:pressed {
    background: #b91c1c;
    padding-top: 8px;
    padding-left: 13px;
}
"""


STATUS_AND_INLINE_INPUT_QSS = """
QStatusBar {
    border-left: 0;
    border-right: 0;
    border-bottom: 0;
    border-radius: 0;
    padding: 5px 10px;
    min-height: 28px;
}
QStatusBar::item {
    border: 0;
}
QPushButton#themeToggle {
    min-width: 32px;
    max-width: 32px;
    min-height: 26px;
    max-height: 26px;
    padding: 0;
    margin: 0 4px 0 8px;
    outline: 0;
}
QLineEdit#tableEditor {
    background: #0d1624;
    color: #e8eef7;
    border: none;
    border-radius: 0;
    outline: 0;
    padding: 2px 7px;
    min-height: 24px;
    selection-background-color: rgba(91,158,255, 0.50);
}
QLineEdit#tableEditor:focus {
    border: none;
    background: #0d1624;
}
"""


# ════════════════════════════════════════════════════════════════════════════
# UNIFIED_PREMIUM_QSS — nadrzędna warstwa designu (E1 redesignu)
# Stosowana ostatnia w stosie QSS dla dark mode; nadpisuje konflikty z
# poprzednich warstw (DARK_QSS + PREMIUM_GLASS_QSS + LIQUID_DARK_QSS).
#
# Tokeny:
#   surface.1 = rgba(255,255,255,0.025)   surface.2 = rgba(255,255,255,0.045)
#   surface.3 = rgba(255,255,255,0.075)   surface.tint = rgba(91,158,255,0.04)
#   border.subtle = rgba(255,255,255,0.06)   border.default = rgba(255,255,255,0.09)
#   border.emphasis = rgba(255,255,255,0.16) border.focus = rgba(91,158,255,0.55)
#   text.primary = #e8eef7   text.secondary = #9fb0c6   text.muted = #6a7a91
#   accent: #5b9eff / #4a8de8 / #7ab4ff
#   success #4ade80   danger #f87171
#   radius: sm=8 md=12 lg=16 xl=20
#   spacing: 4 8 12 16 20 24 32
# ════════════════════════════════════════════════════════════════════════════
UNIFIED_PREMIUM_QSS = """
/* ── Tło aplikacji ───────────────────────────────────────────────────── */
QMainWindow, QDialog, QWidget#appRoot, QStackedWidget#mainStack,
QWidget#workspaceRoot, QWidget#workspacePanel {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #0a1020, stop:1 #060912);
}

/* ── Topbar — subtle glass strip ─────────────────────────────────────── */
QWidget#appHeader {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(255,255,255, 0.055),
        stop:1 rgba(255,255,255, 0.012));
    border: 0;
    border-bottom: 1px solid rgba(255,255,255, 0.08);
    min-height: 68px;
}
QLabel#brandIcon { background: transparent; padding: 0 2px; }
QLabel#brandTitle  { color: #e8eef7; font-size: 15pt; font-weight: 800; letter-spacing: -0.2px; background: transparent; }
QLabel#brandVersion { color: #6a7a91; font-size: 9pt; font-weight: 600; background: transparent; padding-left: 4px; }
QLabel#brandSubtitle { color: #6a7a91; font-size: 8pt;  font-weight: 600; background: transparent; }
QWidget#brandBlock { background: transparent; }

/* ── Tabki nawigacyjne w topbarze ────────────────────────────────────── */
QPushButton#toolbarButton:checked {
    background: rgba(91,158,255, 0.14);
    color: #ffffff;
    border: 1px solid rgba(91,158,255, 0.45);
}

/* ── Karty (uniwersalne) — glass morphism z górnym highlight ─────────── */
QWidget#sectionCard, QWidget#settingsCard, QWidget#summaryPanel,
QWidget#controlPanel {
    background: qlineargradient(x1:0, y1:0, x2:0.5, y2:1,
        stop:0.00 rgba(255,255,255, 0.085),
        stop:0.06 rgba(255,255,255, 0.048),
        stop:0.50 rgba(255,255,255, 0.028),
        stop:1.00 rgba(255,255,255, 0.015));
    border: 1px solid rgba(255,255,255, 0.09);
    border-top-color: rgba(255,255,255, 0.22);
    border-radius: 18px;
}

/* ── Tytuł sekcji ────────────────────────────────────────────────────── */
QLabel#sectionTitle {
    background: transparent;
    color: #e8eef7;
    font-size: 11pt;
    font-weight: 700;
    letter-spacing: 0.1px;
    padding: 0 0 4px 0;
}
QLabel#settingsTitle { color: #e8eef7; font-size: 14pt; font-weight: 800; }
QLabel#compactLabel  { color: #9fb0c6; font-size: 9pt; font-weight: 600; letter-spacing: 0.4px; text-transform: uppercase; background: transparent; }
QLabel#unitLabel     { color: #6a7a91; font-size: 9pt; background: transparent; }

/* ── Przyciski: baza ─────────────────────────────────────────────────── */
QPushButton {
    min-height: 34px;
    border-radius: 10px;
    padding: 0 16px;
    font-weight: 650;
    font-size: 10pt;
}

/* Przyciski toolbarButton / smallButton / navTab / themeToggle = SECONDARY */
QPushButton#toolbarButton, QPushButton#smallButton, QPushButton#navTab,
QPushButton#themeToggle {
    background: rgba(255,255,255, 0.045);
    color: #e8eef7;
    border: 1px solid rgba(255,255,255, 0.09);
}
QPushButton#toolbarButton:hover, QPushButton#smallButton:hover,
QPushButton#navTab:hover, QPushButton#themeToggle:hover {
    background: rgba(255,255,255, 0.075);
    border-color: rgba(255,255,255, 0.16);
    color: #ffffff;
}
QPushButton#toolbarButton:pressed, QPushButton#smallButton:pressed,
QPushButton#navTab:pressed, QPushButton#themeToggle:pressed {
    background: rgba(255,255,255, 0.025);
    /* żaden padding shift — używamy _ButtonClickEffect z opacity dip */
}

/* Active state dla zakładek nawigacji */
QPushButton#navTab:checked {
    background: rgba(91,158,255, 0.12);
    color: #ffffff;
    border: 1px solid rgba(91,158,255, 0.45);
}

/* Przyciski Primary: toolbarPrimary, primaryButton, primaryCalculate */
QPushButton#toolbarPrimary, QPushButton#primaryButton, QPushButton#primaryCalculate {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #7ab4ff, stop:1 #4a8de8);
    color: #ffffff;
    border: 1px solid rgba(91,158,255, 0.55);
    font-weight: 700;
}
QPushButton#toolbarPrimary:hover, QPushButton#primaryButton:hover, QPushButton#primaryCalculate:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #8ec0ff, stop:1 #5b9eff);
    border-color: rgba(122,180,255, 0.70);
}
QPushButton#toolbarPrimary:pressed, QPushButton#primaryButton:pressed, QPushButton#primaryCalculate:pressed {
    background: #4a8de8;
}
QPushButton#primaryCalculate {
    min-height: 44px;
    font-size: 11pt;
    border-radius: 12px;
}
QPushButton:disabled {
    background: rgba(255,255,255, 0.03);
    color: #4c5870;
    border-color: rgba(255,255,255, 0.05);
}

/* ── Inputy — glass tinted surface ───────────────────────────────────── */
QLineEdit, QTextEdit, QPlainTextEdit,
QSpinBox, QDoubleSpinBox, QComboBox,
QSpinBox#premiumInput, QDoubleSpinBox#premiumInput, QComboBox#premiumInput {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(255,255,255, 0.038),
        stop:1 rgba(255,255,255, 0.018));
    color: #e8eef7;
    border: 1px solid rgba(255,255,255, 0.08);
    border-top-color: rgba(255,255,255, 0.14);
    border-radius: 12px;
    padding: 8px 12px;
    selection-background-color: rgba(91,158,255, 0.50);
    min-height: 22px;
}
QLineEdit:hover, QTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {
    border-color: rgba(255,255,255, 0.16);
    border-top-color: rgba(255,255,255, 0.28);
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid rgba(91,158,255, 0.55);
    border-top-color: rgba(140,200,255, 0.75);
    background: rgba(91,158,255, 0.05);
}
QComboBox::drop-down,
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    border: 0; width: 22px; background: transparent;
}
QComboBox QAbstractItemView {
    background: #0d1424;
    color: #e8eef7;
    border: 1px solid rgba(255,255,255, 0.12);
    border-radius: 10px;
    padding: 6px;
    selection-background-color: rgba(91,158,255, 0.25);
    outline: 0;
}

/* ── Tabele (premium data table) ─────────────────────────────────────── */
QTableWidget, QTableView, QTableWidget#partsTable {
    background: transparent;
    alternate-background-color: rgba(255,255,255, 0.025);
    color: #e8eef7;
    gridline-color: transparent;
    selection-background-color: rgba(91,158,255, 0.18);
    selection-color: #ffffff;
    border: 1px solid rgba(255,255,255, 0.06);
    border-radius: 12px;
    outline: 0;
}
QTableWidget::item, QTableView::item {
    padding: 6px 10px;
    border-bottom: 1px solid rgba(255,255,255, 0.04);
}
QTableWidget::item:hover {
    background: rgba(91,158,255, 0.05);
}
QHeaderView::section {
    background: rgba(255,255,255, 0.03);
    color: #9fb0c6;
    font-size: 8pt;
    font-weight: 700;
    letter-spacing: 0.6px;
    padding: 8px 10px;
    border: 0;
    border-bottom: 1px solid rgba(255,255,255, 0.08);
    text-transform: uppercase;
}

/* ── Warning banner / alerty ─────────────────────────────────────────── */
QLabel#warningBanner {
    background: rgba(248,113,113, 0.07);
    color: #fecaca;
    border: 1px solid rgba(248,113,113, 0.30);
    border-radius: 12px;
    padding: 12px 16px;
    font-weight: 600;
    font-size: 10pt;
}

/* ── Status bar i metryki ────────────────────────────────────────────── */
QStatusBar {
    background: rgba(255,255,255, 0.02);
    color: #9fb0c6;
    border-top: 1px solid rgba(255,255,255, 0.06);
    min-height: 32px;
    padding: 0 12px;
}
QStatusBar QLabel { color: #9fb0c6; }

/* ── Scrollbary subtelne ─────────────────────────────────────────────── */
QScrollBar:vertical, QScrollBar:horizontal {
    background: transparent; border: 0;
}
QScrollBar:vertical { width: 10px; }
QScrollBar:horizontal { height: 10px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: rgba(255,255,255, 0.10);
    border-radius: 5px;
    min-height: 24px; min-width: 24px;
}
QScrollBar::handle:hover { background: rgba(255,255,255, 0.18); }
QScrollBar::add-line, QScrollBar::sub-line { background: transparent; border: 0; height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ── Menu ────────────────────────────────────────────────────────────── */
QMenu {
    background: #0d1424;
    color: #e8eef7;
    border: 1px solid rgba(255,255,255, 0.10);
    border-radius: 12px;
    padding: 6px;
}
QMenu::item { padding: 8px 18px; border-radius: 8px; }
QMenu::item:selected { background: rgba(91,158,255, 0.18); }

/* ── Tooltip ─────────────────────────────────────────────────────────── */
QToolTip {
    background: #0d1424;
    color: #e8eef7;
    border: 1px solid rgba(255,255,255, 0.12);
    border-radius: 8px;
    padding: 6px 10px;
}

/* ── Zoom controls — vertical glass pill ────────────────────────────────
   Four stacked buttons (+  −  ⛶  ⊙) floating in the bottom-right corner
   of the canvas.  Heavy blur backdrop is faked with a dark semi-transparent
   fill; the top highlight border and subtle inner glow give depth. */
QWidget#zoomControls {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0  rgba(255,255,255, 0.085),
        stop:0.08 rgba(12, 20, 38, 0.86),
        stop:1  rgba(8,  14, 28, 0.90));
    border: 1px solid rgba(255,255,255, 0.14);
    border-top-color: rgba(255,255,255, 0.30);
    border-radius: 20px;
}
QPushButton#zoomButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(255,255,255, 0.10),
        stop:1 rgba(255,255,255, 0.03));
    color: rgba(210, 228, 252, 0.88);
    border: 1px solid rgba(255,255,255, 0.10);
    border-top-color: rgba(255,255,255, 0.20);
    border-radius: 14px;
    min-width: 44px;
    max-width: 44px;
    min-height: 44px;
    max-height: 44px;
    padding: 0;
    font-size: 14pt;
    font-weight: 600;
}
QPushButton#zoomButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(122,186,255, 0.22),
        stop:1 rgba(91,158,255,  0.08));
    border-color: rgba(91,158,255, 0.55);
    border-top-color: rgba(160,210,255, 0.75);
    color: #ffffff;
}
QPushButton#zoomButton:pressed {
    background: rgba(91,158,255, 0.16);
    border-color: rgba(91,158,255, 0.45);
    color: #e0f0ff;
    padding-top: 2px;
}
"""


REFERENCE_DARK_QSS = """
/* Reference redesign layer: dark production tool UI inspired by the provided mockup. */
QMainWindow, QDialog, QWidget#appRoot, QStackedWidget#mainStack,
QWidget#workspaceRoot, QWidget#workspacePanel {
    background: #0a111c;
    color: #e8eef7;
}
QWidget#appRoot {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #0c1421, stop:0.42 #0a111c, stop:1 #060b13);
}
QWidget#sideRail {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(18, 30, 48, 0.86), stop:1 rgba(8, 15, 26, 0.96));
    border-right: 1px solid rgba(148,163,184,0.12);
}
QWidget#sideBrand {
    background: transparent;
    border-bottom: 1px solid rgba(148,163,184,0.13);
}
QLabel#sideLogo {
    background: transparent;
    border: 0;
}
QLabel#sideTitle {
    background: transparent;
    color: #f8fafc;
    font-size: 15pt;
    font-weight: 850;
}
QLabel#sideVersion, QLabel#sideMuted {
    background: transparent;
    color: #8696ab;
    font-size: 8.5pt;
    font-weight: 600;
}
QPushButton#sideNavButton {
    background: transparent;
    color: #d8e2ef;
    border: 1px solid transparent;
    border-radius: 8px;
    min-height: 38px;
    padding: 0 14px;
    text-align: left;
    font-size: 10pt;
    font-weight: 650;
}
QPushButton#sideNavButton:hover {
    background: rgba(59,130,246,0.11);
    color: #ffffff;
    border-color: rgba(148,163,184,0.10);
}
QPushButton#sideNavButton:checked {
    background: rgba(59,130,246,0.17);
    color: #6da7ff;
    border-left: 3px solid #2f80ff;
}
QWidget#sideModeCard {
    background: rgba(255,255,255,0.035);
    border: 1px solid rgba(148,163,184,0.12);
    border-radius: 8px;
}
QPushButton#sidePrimaryButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #3478f6, stop:1 #1f5edb);
    color: #ffffff;
    border: 1px solid rgba(91,158,255,0.58);
    border-radius: 8px;
    min-height: 38px;
    padding: 0 12px;
    text-align: left;
    font-weight: 800;
}
QPushButton#sidePrimaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #4c8bff, stop:1 #256ef0);
}
QPushButton#sideActionButton {
    background: rgba(255,255,255,0.030);
    color: #e2e8f0;
    border: 1px solid rgba(148,163,184,0.13);
    border-radius: 8px;
    min-height: 36px;
    padding: 0 12px;
    text-align: left;
    font-weight: 700;
}
QPushButton#sideActionButton:hover {
    background: rgba(255,255,255,0.060);
    border-color: rgba(91,158,255,0.36);
}
QPushButton#sideModeCard {
    background: rgba(255,255,255,0.035);
    color: #f8fafc;
    border: 1px solid rgba(148,163,184,0.12);
    border-radius: 8px;
    min-height: 52px;
    padding: 7px 12px;
    text-align: left;
    font-weight: 800;
}
QPushButton#sideModeCard:hover {
    background: rgba(59,130,246,0.12);
    border-color: rgba(91,158,255,0.34);
}
QWidget#appHeader {
    background: rgba(255,255,255,0.018);
    border: 1px solid rgba(148,163,184,0.10);
    border-radius: 8px;
    min-height: 58px;
}
QWidget#brandBlock, QLabel#brandIcon, QLabel#brandTitle, QLabel#brandVersion, QLabel#brandSubtitle {
    background: transparent;
}
QLabel#brandTitle { color: #f8fafc; font-size: 13pt; font-weight: 800; }
QLabel#brandVersion, QLabel#brandSubtitle { color: #8390a4; }
QWidget#controlPanel {
    background: transparent;
    border: 0;
}
QWidget#sectionCard, QWidget#settingsCard, QWidget#summaryPanel {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(22, 34, 52, 0.92), stop:1 rgba(12, 21, 35, 0.96));
    border: 1px solid rgba(148,163,184,0.14);
    border-radius: 8px;
}
QWidget#canvasStack {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(16, 27, 44, 0.96), stop:1 rgba(8, 15, 26, 0.98));
    border: 1px solid rgba(148,163,184,0.15);
    border-radius: 8px;
}
QGraphicsView#layoutView {
    background: #0a111c;
    border: 0;
    border-radius: 8px;
}
QLabel#sectionTitle {
    color: #f2f6fb;
    font-size: 11pt;
    font-weight: 800;
    padding: 0 0 7px 0;
}
QLabel#compactLabel {
    color: #9ba9bb;
    font-size: 8pt;
    font-weight: 800;
    letter-spacing: 0.7px;
    text-transform: uppercase;
}
QPushButton {
    border-radius: 8px;
    min-height: 34px;
    padding: 0 14px;
    font-weight: 700;
}
QPushButton#toolbarPrimary, QPushButton#primaryButton, QPushButton#primaryCalculate {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #3478f6, stop:1 #1f5edb);
    color: #ffffff;
    border: 1px solid rgba(91,158,255,0.58);
}
QPushButton#toolbarPrimary:hover, QPushButton#primaryButton:hover, QPushButton#primaryCalculate:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #4c8bff, stop:1 #256ef0);
    border-color: rgba(125,184,255,0.78);
}
QPushButton#toolbarButton, QPushButton#smallButton, QPushButton#navTab, QPushButton#themeToggle {
    background: rgba(255,255,255,0.030);
    color: #e2e8f0;
    border: 1px solid rgba(148,163,184,0.13);
}
QPushButton#toolbarButton:hover, QPushButton#smallButton:hover, QPushButton#navTab:hover, QPushButton#themeToggle:hover {
    background: rgba(255,255,255,0.060);
    border-color: rgba(91,158,255,0.36);
}
QPushButton#primaryCalculate {
    min-height: 50px;
    font-size: 11pt;
}
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox,
QSpinBox#premiumInput, QDoubleSpinBox#premiumInput, QComboBox#premiumInput {
    background: rgba(5, 12, 22, 0.78);
    color: #e8eef7;
    border: 1px solid rgba(148,163,184,0.16);
    border-radius: 7px;
    padding: 7px 10px;
    min-height: 24px;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid rgba(59,130,246,0.72);
    background: rgba(9, 19, 34, 0.92);
}
QTableWidget, QTableView, QTableWidget#partsTable {
    background: rgba(6, 14, 25, 0.70);
    alternate-background-color: rgba(255,255,255,0.018);
    color: #dce5f1;
    gridline-color: rgba(148,163,184,0.06);
    selection-background-color: rgba(47,128,255,0.24);
    selection-color: #ffffff;
    border: 1px solid rgba(148,163,184,0.12);
    border-radius: 7px;
}
QTableWidget::item, QTableView::item {
    padding: 7px 10px;
    border-bottom: 1px solid rgba(148,163,184,0.055);
}
QHeaderView::section {
    background: rgba(255,255,255,0.028);
    color: #a8b5c7;
    border: 0;
    border-bottom: 1px solid rgba(148,163,184,0.12);
    padding: 8px 10px;
    font-size: 8pt;
    font-weight: 800;
    letter-spacing: 0.4px;
}
QWidget#sheetNavBar {
    background: rgba(7, 14, 25, 0.88);
    border-bottom: 1px solid rgba(148,163,184,0.12);
}
QWidget#zoomControls {
    background: rgba(12, 21, 34, 0.94);
    border: 1px solid rgba(148,163,184,0.16);
    border-radius: 8px;
}
QPushButton#zoomButton {
    min-width: 34px;
    max-width: 34px;
    min-height: 34px;
    max-height: 34px;
    border-radius: 6px;
    background: rgba(255,255,255,0.035);
}
QStatusBar {
    background: transparent;
    color: #8f9daf;
    border-top: 1px solid rgba(148,163,184,0.10);
    min-height: 28px;
}
QSplitter::handle { background: transparent; width: 12px; }


/* Reference pass 2: closer to target proportions. */
QWidget#mainColumn { background: transparent; }
QWidget#appHeader {
    background: transparent;
    border: 0;
    min-height: 54px;
    max-height: 54px;
}
QWidget#topbarBrand, QLabel#topbarLogo, QLabel#topbarTitle, QLabel#topbarVersion {
    background: transparent;
    border: 0;
}
QLabel#topbarLogo {
    min-width: 38px;
    max-width: 38px;
    min-height: 38px;
    max-height: 38px;
}
QLabel#topbarTitle {
    color: #f8fafc;
    font-size: 10pt;
    font-weight: 850;
}
QLabel#topbarVersion {
    color: #8796aa;
    font-size: 8pt;
    font-weight: 650;
}
QWidget#sideRail {
    border-right: 1px solid rgba(148,163,184,0.10);
}
QWidget#sideBrand {
    border-bottom: 1px solid rgba(148,163,184,0.12);
}
QLabel#sideTitle { font-size: 12pt; }
QLabel#sideLogo { min-width: 52px; max-width: 52px; min-height: 52px; max-height: 52px; }
QPushButton#sideNavButton {
    min-height: 42px;
    padding: 0 13px;
}
QPushButton#topbarPrimary,
QPushButton#topbarButton,
QPushButton#topbarModeButton,
QPushButton#topbarNavButton,
QPushButton#topbarTutorial {
    border-radius: 8px;
    min-height: 38px;
    max-height: 38px;
    padding: 0 14px;
    font-size: 9.5pt;
    font-weight: 800;
}
QPushButton#topbarPrimary {
    min-width: 142px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #3478f6, stop:1 #1f5edb);
    color: #ffffff;
    border: 1px solid rgba(91,158,255,0.64);
}
QPushButton#topbarPrimary:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #4c8bff, stop:1 #256ef0);
    border-color: rgba(125,184,255,0.82);
}
QPushButton#topbarButton,
QPushButton#topbarModeButton,
QPushButton#topbarNavButton {
    background: rgba(255,255,255,0.032);
    color: #e8eef7;
    border: 1px solid rgba(148,163,184,0.14);
}
QPushButton#topbarButton:hover,
QPushButton#topbarModeButton:hover,
QPushButton#topbarNavButton:hover {
    background: rgba(255,255,255,0.064);
    border-color: rgba(91,158,255,0.38);
}
QPushButton#topbarNavButton:checked {
    background: rgba(59,130,246,0.17);
    color: #6da7ff;
    border-color: rgba(91,158,255,0.42);
}
QPushButton#topbarNavButton {
    min-width: 88px;
}
QPushButton#topbarTutorial {
    min-width: 112px;
    background: #b91c1c;
    color: #ffffff;
    border: 1px solid #ef4444;
}
QPushButton#topbarTutorial:hover {
    background: #dc2626;
    border-color: #f87171;
}
QPushButton#topbarButton {
    min-width: 112px;
}
QPushButton#topbarModeButton {
    min-width: 116px;
}
QWidget#sectionCard {
    border-radius: 8px;
    background: rgba(18, 30, 48, 0.86);
}
QWidget#previewHeader {
    background: rgba(18, 30, 48, 0.86);
    border: 1px solid rgba(148,163,184,0.14);
    border-bottom: 0;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}
QWidget#previewMetrics {
    background: rgba(255,255,255,0.035);
    border: 1px solid rgba(148,163,184,0.11);
    border-radius: 8px;
}
QWidget#previewMetricBox { background: transparent; min-width: 112px; }
QLabel#previewMetricLabel {
    background: transparent;
    color: #9ba9bb;
    font-size: 8pt;
    font-weight: 650;
}
QLabel#previewMetricValue {
    background: transparent;
    color: #f2f6fb;
    font-size: 10pt;
    font-weight: 850;
}
QWidget#previewMetricBox:first-child QLabel#previewMetricValue { color: #6da7ff; }
QWidget#canvasStack {
    border-top-left-radius: 0;
    border-top-right-radius: 0;
    border-top: 1px solid rgba(148,163,184,0.10);
}
QGraphicsView#layoutView {
    border-top-left-radius: 0;
    border-top-right-radius: 0;
}
QPushButton#smallButton {
    min-height: 34px;
    padding-left: 12px;
    padding-right: 12px;
    font-size: 9pt;
}
QTableWidget, QTableView, QTableWidget#partsTable {
    font-size: 9pt;
}
"""


SMART_STOCK_DARK_QSS = """
QCheckBox#smartStockMode {
    background: rgba(255,255,255,0.045);
    color: #dbe7f5;
    border: 1px solid rgba(148,163,184,0.18);
    border-radius: 8px;
    padding: 5px 10px;
    spacing: 8px;
    font-weight: 700;
}
QCheckBox#smartStockMode:hover {
    background: rgba(59,130,246,0.10);
    border-color: rgba(91,158,255,0.45);
}
QCheckBox#smartStockMode:checked {
    background: rgba(37,99,235,0.18);
    color: #f8fbff;
    border-color: rgba(91,158,255,0.62);
}
QCheckBox#smartStockMode::indicator {
    width: 15px;
    height: 15px;
    border-radius: 5px;
    border: 1px solid rgba(148,163,184,0.58);
    background: #0b1524;
}
QCheckBox#smartStockMode::indicator:checked {
    background: #2f80ff;
    border-color: #79b2ff;
}
"""

SMART_STOCK_LIGHT_QSS = """
QCheckBox#smartStockMode {
    background: #f7faff;
    color: #243247;
    border: 1px solid rgba(15,23,42,0.14);
    border-radius: 8px;
    padding: 5px 10px;
    spacing: 8px;
    font-weight: 700;
}
QCheckBox#smartStockMode:hover {
    background: #eef5ff;
    border-color: rgba(37,99,235,0.40);
}
QCheckBox#smartStockMode:checked {
    background: #e7f0ff;
    color: #163b78;
    border-color: rgba(37,99,235,0.48);
}
QCheckBox#smartStockMode::indicator {
    width: 15px;
    height: 15px;
    border-radius: 5px;
    border: 1px solid rgba(15,23,42,0.28);
    background: #ffffff;
}
QCheckBox#smartStockMode::indicator:checked {
    background: #2f80ff;
    border-color: #1d63db;
}
"""

TABLE_ENTRY_INTERACTION_QSS = """
QTableWidget#partsTable::item:hover:!selected {
    background: transparent;
}
QTableWidget#partsTable::item:selected {
    background: rgba(37, 99, 235, 0.24);
    border: 1px solid rgba(96, 165, 250, 0.88);
    border-radius: 6px;
}
"""


def _refresh_v4_qss(light: bool) -> str:
    background, surface, field = ("#f3f6fb", "#ffffff", "#f8fafd") if light else ("#0e1523", "#172132", "#111a2a")
    text, muted, border = ("#1f3048", "#607089", "#dce3ee") if light else ("#e7eef9", "#a6b5cb", "#2b3a51")
    return f"""
    QMainWindow, QWidget#appRoot, QWidget#workspacePanel {{ background: {background}; color: {text}; }}
    QWidget#sectionCard, QWidget#settingsCard, QWidget#summaryPanel {{
        background: {surface}; border: 1px solid {border}; border-radius: 12px;
    }}
    QWidget#appHeader, QWidget#canvasStack {{ background: {background}; border: 1px solid {border}; border-radius: 10px; }}
    QTableWidget, QTableWidget#partsTable {{ background: {surface}; alternate-background-color: {field};
        color: {text}; border: 1px solid {border}; border-radius: 8px; gridline-color: {border}; }}
    QHeaderView::section {{ background: {field}; color: {muted}; padding: 7px 6px;
        border: 0; border-bottom: 1px solid {border}; font-weight: 600; }}
    QTableWidget::item {{ padding: 4px 5px; border-bottom: 1px solid {border}; }}
    QPushButton#toolbarButton, QPushButton#smallButton, QPushButton#navTab, QPushButton#themeToggle {{
        background: {surface}; color: {text}; border: 1px solid {border}; border-radius: 8px; font-weight: 600;
    }}
    QPushButton#toolbarButton:hover, QPushButton#smallButton:hover, QPushButton#navTab:hover {{
        background: {field}; border-color: #779fe5;
    }}
    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{ background: {field}; color: {text}; border-color: {border}; }}
    QComboBox QAbstractItemView, QMenu {{ background: {surface}; color: {text}; border: 1px solid {border}; }}
    QMenu::item:selected {{ background: #3268bb; color: white; }}
    QLabel#sectionTitle {{ color: {text}; font-weight: 600; }}
    QLabel#previewMetricLabel {{ color: {muted}; font-size: 9pt; }}
    QLabel#previewMetricValue {{ color: {text}; font-size: 15pt; font-weight: 600; }}
    QToolTip {{ background: {surface}; color: {text}; border: 1px solid {border}; padding: 6px; }}
    QScrollBar:horizontal {{ height: 8px; background: {field}; }}
    QScrollBar:vertical {{ width: 8px; background: {field}; }}
    QScrollBar::handle {{ background: #889ab3; border-radius: 4px; min-width: 20px; min-height: 20px; }}
    """


def apply_theme(app: QCoreApplication | None, theme: str = "dark") -> None:
    if not isinstance(app, QApplication):
        raise RuntimeError("Motyw wymaga uruchomionej aplikacji Qt.")
    normalized = "light" if theme == "light" else "dark"
    app.setProperty("theme", normalized)
    accent = "sport" if app.property("optimization_mode") == "sport" else "comfort"
    if normalized == "light":
        qss = LIGHT_QSS + LIGHT_PREMIUM_GLASS_QSS + SMART_STOCK_LIGHT_QSS
    else:
        qss = DARK_QSS + PREMIUM_GLASS_QSS + LIQUID_DARK_QSS + SMART_STOCK_DARK_QSS
    if accent == "sport":
        qss += SPORT_ACCENT_QSS
    qss += STATUS_AND_INLINE_INPUT_QSS
    # Unified premium override layer — last in cascade, wins all conflicts.
    if normalized == "dark":
        qss += UNIFIED_PREMIUM_QSS
        qss += REFERENCE_DARK_QSS
    qss += TABLE_ENTRY_INTERACTION_QSS
    qss += _refresh_v4_qss(normalized == "light")
    app.setStyleSheet(qss)
    for widget in app.allWidgets():
        apply_button_cursors(widget)


def apply_accent_mode(app: QCoreApplication | None, mode: str = "comfort") -> None:
    if not isinstance(app, QApplication):
        raise RuntimeError("Motyw wymaga uruchomionej aplikacji Qt.")
    app.setProperty("optimization_mode", "sport" if mode == "sport" else "comfort")
    apply_theme(app, str(app.property("theme") or "dark"))


def apply_button_cursors(root: QWidget) -> None:
    buttons = []
    if isinstance(root, QAbstractButton):
        buttons.append(root)
    buttons.extend(root.findChildren(QAbstractButton))
    for button in buttons:
        button.setCursor(Qt.CursorShape.PointingHandCursor)


def apply_native_title_bar(window: QWidget, theme: str = "dark") -> None:
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        value = ctypes.c_int(1 if theme != "light" else 0)
        for attribute in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_int(attribute),
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
    except Exception:
        return
