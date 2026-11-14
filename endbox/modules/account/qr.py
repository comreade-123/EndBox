# -*- coding: utf-8 -*-
"""二维码矩阵生成：把文本交给 vendor 的 QRCode，输出给 QRCanvas 的布尔矩阵。

只用纯计算，不依赖 Qt、不写图片文件；调用方（视图）在 UI 线程里调用也足够快。
"""
from __future__ import annotations

from endbox.core.text import t
from endbox.modules.account.models import AccountError
from endbox.thirdparty.qrcode.constants import ERROR_CORRECT_M
from endbox.thirdparty.qrcode.main import QRCode

# 交由 QRCanvas 自己留白（quiet zone），这里不再加边框，避免双重留白。
QR_BORDER = 0


def make_matrix(data: str, *, error_correction: int = ERROR_CORRECT_M) -> list[list[bool]]:
    """生成二维码模块矩阵：每行每列都是 bool，True = 深色模块。

    内容为空或过长时抛 AccountError（界面显示人话），不向上抛底层的 DataOverflowError。
    """
    text = str(data or "").strip()
    if not text:
        raise AccountError(
            t("account.error.qr.empty"), hint=t("account.error.qr.empty_hint")
        )
    qr = QRCode(error_correction=error_correction, box_size=8, border=QR_BORDER)
    try:
        qr.add_data(text)
        qr.make(fit=True)
        matrix = qr.get_matrix()
    except Exception as exc:  # noqa: BLE001 - vendor 的实现可能抛多种异常
        raise AccountError(
            t("account.error.qr.failed"), hint=t("account.error.qr.failed_hint")
        ) from exc
    return [[bool(value) for value in row] for row in matrix]


def is_valid_matrix(matrix: list[list[bool]] | None) -> bool:
    """矩阵必须是正方形且非空，否则 QRCanvas 不该收到它。"""
    if not matrix:
        return False
    size = len(matrix)
    return size > 0 and all(isinstance(row, list) and len(row) == size for row in matrix)
