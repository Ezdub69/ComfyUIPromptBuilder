"""Image downscale + base64 data-URL encoding for the Krea 2 Assistant tab's
vision mode. Uses Qt's own QImage rather than adding Pillow as a dependency -
this project has exactly two (PySide6, pyspellchecker) today.

Resizes so the longest side fits max_dim (keeping aspect ratio), re-encodes
as JPEG at the given quality, and returns a data: URL ready to drop
straight into an OpenAI-style image_url message part.
"""

import base64

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage


def downscale_image_to_data_url(path, max_dim=768, quality=85):
    img = QImage(path)
    if img.isNull():
        raise ValueError(f"Failed to load image: {path}")

    w, h = img.width(), img.height()
    longest = max(w, h)
    if longest > max_dim:
        scale = max_dim / longest
        img = img.scaled(
            max(1, round(w * scale)), max(1, round(h * scale)),
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )

    byte_array = QByteArray()
    buffer = QBuffer(byte_array)
    buffer.open(QIODevice.WriteOnly)
    if not img.save(buffer, "JPEG", quality):
        buffer.close()
        raise ValueError(f"Failed to encode image as JPEG: {path}")
    buffer.close()

    b64 = base64.b64encode(bytes(byte_array)).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"
