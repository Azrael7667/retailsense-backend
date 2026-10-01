import cv2
import numpy as np
from PIL import Image


def _to_gray(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2GRAY)


def upscale(gray: np.ndarray, min_width: int = 1800) -> np.ndarray:
    """Tesseract works best when characters are ~30px tall. Upscale small photos."""
    h, w = gray.shape
    if w < min_width:
        s = min_width / w
        gray = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    return gray


def estimate_skew(gray: np.ndarray) -> float:
    """Median angle of long near-horizontal lines (invoice table borders work great)."""
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=150,
        minLineLength=gray.shape[1] // 4, maxLineGap=20,
    )
    if lines is None:
        return 0.0
    angles = []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if abs(a) <= 15:
            angles.append(a)
    return float(np.median(angles)) if angles else 0.0


def rotate(gray: np.ndarray, angle: float) -> np.ndarray:
    if abs(angle) < 0.3:
        return gray
    h, w = gray.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(
        gray, M, (w, h), flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT, borderValue=255,
    )


def remove_table_lines(binary_inv: np.ndarray) -> np.ndarray:
    """binary_inv: text = white (255) on black. Erases long horizontal/vertical lines."""
    h, w = binary_inv.shape
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 20, 40), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 40, 40)))
    hl = cv2.morphologyEx(binary_inv, cv2.MORPH_OPEN, hk)
    vl = cv2.morphologyEx(binary_inv, cv2.MORPH_OPEN, vk)
    lines = cv2.dilate(cv2.bitwise_or(hl, vl), np.ones((3, 3), np.uint8))
    return cv2.bitwise_and(binary_inv, cv2.bitwise_not(lines))


def preprocess(img: Image.Image, strip_lines: bool = True) -> Image.Image:
    gray = _to_gray(img)
    gray = upscale(gray)
    gray = rotate(gray, estimate_skew(gray))
    gray = cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 15
    )
    if strip_lines:
        inv = cv2.bitwise_not(binary)
        inv = remove_table_lines(inv)
        binary = cv2.bitwise_not(inv)
    binary = cv2.copyMakeBorder(binary, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return Image.fromarray(binary)
