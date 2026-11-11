"""Render sm809/icon.png (256x256). Run: QT_QPA_PLATFORM=offscreen python3 tools/make_icon.py"""
import sys
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

app = QGuiApplication(sys.argv)
img = QImage(256, 256, QImage.Format.Format_ARGB32)
img.fill(Qt.GlobalColor.transparent)
g = QPainter(img)
g.setRenderHint(QPainter.RenderHint.Antialiasing)

body = QPainterPath()
body.addRoundedRect(QRectF(58, 18, 140, 220), 70, 70)
g.fillPath(body, QColor("#1d2025"))
g.setPen(QPen(QColor("#3a3f47"), 4))
g.drawPath(body)
g.setPen(QPen(QColor("#3a3f47"), 3))
g.drawLine(128, 22, 128, 100)  # button split
g.setPen(Qt.PenStyle.NoPen)
g.setBrush(QColor("#39ff6a"))
g.drawRoundedRect(QRectF(119, 44, 18, 40), 9, 9)  # wheel
for i in range(3):  # side keypad
    for j in range(3):
        g.drawRoundedRect(QRectF(68 + j * 11, 122 + i * 22, 9, 17), 3, 3)
g.end()
out = Path(__file__).resolve().parent.parent / "sm809" / "icon.png"
img.save(str(out))
print("wrote", out)
