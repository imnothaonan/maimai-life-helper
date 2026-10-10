# -*- coding: utf-8 -*-
"""
segno（精简版）—— 仅供本 Skill 内部生成二维码矩阵使用。

来源：segno 1.6.6（https://github.com/heuer/segno）
作者：Lars Heuer
许可：BSD-3-Clause（见同目录 LICENSE）

## 为什么 vendored 进来

本项目坚持「纯 Python 标准库、零第三方依赖」，用户克隆后不应需要
`pip install`。但二维码生成又必须**本地完成** —— 绝不能把支付链接
送给第三方二维码 API，那等于把订单凭证泄露出去。

因此只提取 segno 的编码核心两个文件：
  - encoder.py  （QR 编码：RS 纠错、掩码、格式信息）
  - consts.py   （常量表）

两个文件**只依赖标准库**（operator / functools / itertools / re /
math / codecs / collections / sys），已实测确认。

省略的部分：writers.py（图像输出）、helpers.py、cli.py。
图像输出由本项目的 `mm_qr.py` 自行渲染 SVG / PBM，代码量很小。

## 上游 API 未被修改

`encoder.encode(content, ...)` 是 segno 的原生接口，本文件只是
把它包成 `make()`，方便调用方拿 matrix。
"""

from . import encoder  # noqa: F401
from .encoder import encode  # noqa: F401

__version__ = "1.6.6-vendored(encoder+consts only)"

__all__ = ["encode", "encoder", "__version__"]


def make(content, error="m", version=None, mode=None, mask=None):
    """生成二维码，返回 segno 的 Code 对象。

    参数与 segno 1.6.6 一致，只暴露本项目需要的子集。

    Args:
        content: 要编码的内容（字符串）
        error:   纠错级别 'l'/'m'/'q'/'h'，默认 'm'
                 —— 'm' 在容错与密度间平衡最好，适合 URL
        version: 强制版本（None = 自动选择）
        mode:    编码模式（None = 自动）
        mask:    掩码（None = 自动择优）

    Returns:
        Code 对象，用 `.matrix` 取矩阵（tuple of bytearray，1=黑块）
    """
    return encode(content, error=error, version=version, mode=mode, mask=mask)
