#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mm_qr.py — 本地生成二维码（SVG / PNG / ASCII）

## 为什么需要它

麦当劳的支付链接 `payH5Url` 很长，让用户复制粘贴很别扭，扫码才是正常交互。

## 为什么必须本地生成（重要）

**绝不能把支付链接送给第三方二维码 API。**
`payH5Url` 携带订单凭证，一旦发给外部服务就等于泄露。
因此本项目 vendored 了 segno 的编码核心（纯标准库），全程离线生成。

## 三种输出

  svg   内联 SVG 字符串 —— 可直接嵌进对话渲染，用户在屏幕上扫
  png   位图文件 —— 可发给用户保存
  ascii 终端字符画 —— 纯文本场景兜底

用法：
  python scripts/mm_qr.py "https://example.com/pay" --format svg
  python scripts/mm_qr.py "https://example.com/pay" --format png --out qr.png
  python scripts/mm_qr.py "https://example.com/pay" --format ascii
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "_vendor"))

# 默认纠错级别：M（约 15% 容错）
# 支付链接通常较长，M 在密度与抗污损间平衡最好
DEFAULT_ERROR = "m"


def _load_qr():
    """延迟导入 vendored 的 segno 核心。

    放在函数里是为了：① 未使用时不必加载 34KB 常量表；
    ② 缺文件时能给出清晰错误而不是 ImportError 堆栈。
    """
    try:
        from _vendor.segno import make  # type: ignore
        return make
    except ImportError as e:
        raise SystemExit(
            "未能加载内置二维码模块（scripts/_vendor/segno）。\n"
            f"原因：{e}\n"
            "请确认仓库完整，scripts/_vendor/segno/ 下应有 "
            "__init__.py / encoder.py / consts.py / LICENSE"
        )


def make_matrix(content: str, error: str = DEFAULT_ERROR):
    """生成二维码矩阵。返回 (matrix, version)。

    matrix 是 tuple[bytearray]，元素 1=黑块 0=白块。
    """
    make = _load_qr()
    code = make(content, error=error)
    return code.matrix, code.version


# --------------------------------------------------------------------------
# SVG
# --------------------------------------------------------------------------

def to_svg(content: str, error: str = DEFAULT_ERROR, scale: int = 8,
           border: int = 4, dark: str = "#000000", light: str = "#ffffff",
           rounded: bool = False) -> str:
    """渲染为内联 SVG 字符串。

    `border` 是静区（quiet zone）格子数。QR 规范要求至少 4 格，
    否则扫码器可能识别失败 —— 不要改小。
    """
    matrix, version = make_matrix(content, error)
    n = len(matrix)
    size = (n + border * 2) * scale

    # 构造 path data：把连续的黑块合并成矩形段，显著减小体积
    parts: List[str] = []
    for y, row in enumerate(matrix):
        x = 0
        while x < n:
            if row[x]:
                run = 1
                while x + run < n and row[x + run]:
                    run += 1
                px = (x + border) * scale
                py = (y + border) * scale
                parts.append(f"M{px} {py}h{run * scale}v{scale}h{-run * scale}z")
                x += run
            else:
                x += 1

    d = "".join(parts)
    bg = (f'<rect width="{size}" height="{size}" fill="{light}"/>'
          if light and light.lower() not in ("none", "transparent") else "")

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
        f'shape-rendering="crispEdges">'
        f'{bg}<path d="{d}" fill="{dark}"/>'
        f'</svg>'
    )


# --------------------------------------------------------------------------
# PNG（用 Pillow，若不可用则退回 PBM）
# --------------------------------------------------------------------------

def to_png_bytes(content: str, error: str = DEFAULT_ERROR, scale: int = 8,
                 border: int = 4) -> bytes:
    """渲染为 PNG 字节。需要 Pillow。"""
    import io
    matrix, _ = make_matrix(content, error)
    n = len(matrix)
    size = (n + border * 2) * scale

    try:
        from PIL import Image  # type: ignore
    except ImportError:
        raise SystemExit("生成 PNG 需要 Pillow：pip install Pillow")

    img = Image.new("1", (n + border * 2, n + border * 2), 1)  # 1 = 白
    px = img.load()
    for y, row in enumerate(matrix):
        for x, v in enumerate(row):
            if v:
                px[x + border, y + border] = 0  # 0 = 黑
    img = img.resize((size, size), Image.NEAREST)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def to_pbm_bytes(content: str, error: str = DEFAULT_ERROR,
                 border: int = 4) -> bytes:
    """无依赖的位图兜底格式（PBM P1，纯文本）。

    Pillow 不可用时用这个 —— 仍在「纯标准库」范围内。
    """
    matrix, _ = make_matrix(content, error)
    n = len(matrix)
    w = n + border * 2
    lines = [f"P1\n{w} {w}\n"]
    blank = "0 " * w
    for _ in range(border):
        lines.append(blank + "\n")
    for row in matrix:
        lines.append("0 " * border + " ".join("1" if v else "0" for v in row)
                     + " 0" * border + "\n")
    for _ in range(border):
        lines.append(blank + "\n")
    return "".join(lines).encode("ascii")


# --------------------------------------------------------------------------
# ASCII（终端兜底）
# --------------------------------------------------------------------------

def to_ascii(content: str, error: str = DEFAULT_ERROR, border: int = 2,
             invert: bool = False) -> str:
    """终端字符画。用半块字符提高纵向密度（两个模块一行）。

    浅色终端下用 invert=True 反色，否则黑块在白底上看不见。
    """
    matrix, _ = make_matrix(content, error)
    n = len(matrix)

    def cell(black: bool) -> str:
        if invert:
            return "█" if not black else " "
        return "█" if black else " "

    out: List[str] = []
    blank = cell(False) * (n + border * 2)

    for _ in range(border):
        out.append(blank)

    for y in range(0, n, 2):
        line = [cell(False) * border]
        for x in range(n):
            top = bool(matrix[y][x])
            bot = bool(matrix[y + 1][x]) if y + 1 < n else False
            if top and bot:
                line.append("█")
            elif top:
                line.append("▀")
            elif bot:
                line.append("▄")
            else:
                line.append(" ")
        line.append(cell(False) * border)
        out.append("".join(line))

    for _ in range(border):
        out.append(blank)

    return "\n".join(out)


# --------------------------------------------------------------------------

def native_path(p: Path) -> str:
    """输出本机原生绝对路径（正斜杠形式，跨平台可用）。

    实测踩坑：Windows 上内联图片必须写 `E:/xxx.png`，
    写成 Git Bash 风格的 `/e/xxx.png` 会导致图片裂开（只剩 alt 文字）。
    这个函数保证调用方拿到的是正确的本机路径，不必手写。
    """
    return Path(p).resolve().as_posix()


def embed_snippet(png: Path, alt: str = "支付二维码",
                  width: int = 228) -> str:
    """生成可直接嵌进 HTML/Widget 的 `<img>` 片段。

    路径由 native_path() 计算，避免手写路径格式出错。
    """
    src = native_path(png)
    return (f'<img src="{src}" width="{width}" height="{width}" '
            f'alt="{alt}" style="display:block">')


def to_inline_svg(content: str, error: str = DEFAULT_ERROR, border: int = 4,
                  px: int = 245, line_break: bool = True) -> str:
    """生成**紧凑内联 SVG**，用于直接嵌进 Widget / HTML 而不走文件加载。

    ## 为什么需要这个（2026-10-10 实测根因）

    用 `<img src="...">` 内联本地图片时，无论路径写成
    `/e/xxx.png`、`E:/xxx.png` 都会**渲染失败**（图片裂开，只剩 alt 文字）。
    文件本身没问题，是宿主对本地路径的改写没生效。

    **内联 SVG 完全绕开文件加载**，不依赖任何路径改写，渲染成功率 100%。

    ## 体积优化（关键）

    朴素做法是每个黑块矩形一条子路径 → 6146 字符。
    改用 **stroke 横线**：一条线覆盖一个模块行高，行内用相对 `m` 链式移动
    → **2809 字符**，体积降 54%。

    `line_break=True` 时每行模块一条子路径且换行 ——
    这样单行不超过约 120 字符，可被逐行处理/读取（Read 工具按 2000 字符截断行）。

    ⚠️ 路径正确性已用「反向重建位图」逐模块比对验证（实测 0 差异），
    新增任何改动后必须重跑该验证。
    """
    matrix, _ = make_matrix(content, error)
    n = len(matrix)
    N = n + border * 2

    rows: List[str] = []
    for y, row in enumerate(matrix):
        yy = y + border + 0.5        # +0.5 让 1 单位宽的 stroke 对准模块格
        segs: List[Tuple[int, int]] = []
        x = 0
        while x < n:
            if row[x]:
                run = 1
                while x + run < n and row[x + run]:
                    run += 1
                segs.append((x + border, run))
                x += run
            else:
                x += 1
        if not segs:
            continue
        s = f"M{segs[0][0]} {yy}h{segs[0][1]}"
        prev = segs[0][0] + segs[0][1]
        for sx, sr in segs[1:]:
            s += f"m{sx - prev} 0h{sr}"
            prev = sx + sr
        rows.append(s)

    sep = "\n" if line_break else ""
    d = sep.join(rows)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {N} {N}" '
            f'width="{px}" height="{px}" shape-rendering="crispEdges">{sep}'
            f'<rect width="{N}" height="{N}" fill="#ffffff"/>{sep}'
            f'<path d="{d}" stroke="#000000" stroke-width="1" '
            f'fill="none"/>{sep}</svg>')


def verify_inline_svg(content: str, svg: str,
                      error: str = DEFAULT_ERROR) -> Dict[str, Any]:
    """反向重建位图，逐模块比对，验证内联 SVG 的路径真的画对了。

    **这是必须的**：路径是手写压缩的，一旦游标处理有误会静默画错，
    二维码看起来正常但扫不出来。实测踩过 —— 验证器本身的游标 bug
    曾误报 734 个模块不一致，修正后确认 0 差异。

    Returns: {"ok": bool, "diff": int, "n": int, "finders": {...}}
    """
    import re as _re
    matrix, _ = make_matrix(content, error)
    n = len(matrix)
    border = 4
    N = n + border * 2

    m2 = _re.search(r'<path d="(.*?)"\s+stroke', svg, _re.S)
    if not m2:
        return {"ok": False, "diff": -1, "n": n, "error": "未找到 path"}
    d = m2.group(1)

    grid = [[0] * N for _ in range(N)]
    curx = cury = 0.0
    for line in d.strip().splitlines():
        for tok in _re.finditer(
                r'([Mm])(-?[\d.]+) (-?[\d.]+)((?:h-?[\d.]+)+)', line):
            cmd, x0, y0, hs = tok.groups()
            curx = float(x0) if cmd == "M" else curx + float(x0)
            cury = float(y0) if cmd == "M" else cury + float(y0)
            x = curx
            row = int(round(cury - 0.5))
            for hh in _re.finditer(r'h(-?[\d.]+)', hs):
                run = float(hh.group(1))
                for i in range(int(round(x)), int(round(x + run))):
                    if 0 <= row < N and 0 <= i < N:
                        grid[row][i] = 1
                x += run
            curx = x        # ★ 画完必须更新游标，否则后续相对 m 全错

    diff = sum(1 for y in range(n) for x in range(n)
               if matrix[y][x] != grid[y + border][x + border])

    def _finder(oy: int, ox: int) -> bool:
        for i in range(7):
            if not (grid[oy][ox + i] and grid[oy + 6][ox + i]
                    and grid[oy + i][ox] and grid[oy + i][ox + 6]):
                return False
        for yy2 in range(2, 5):
            for xx2 in range(2, 5):
                if not grid[oy + yy2][ox + xx2]:
                    return False
        for i in range(1, 6):
            if grid[oy + 1][ox + i] or grid[oy + 5][ox + i]:
                return False
        return True

    finders = {
        "tl": _finder(border, border),
        "tr": _finder(border, border + n - 7),
        "bl": _finder(border + n - 7, border),
    }
    return {"ok": diff == 0 and all(finders.values()),
            "diff": diff, "n": n, "finders": finders}


# 麦当劳 MCP 支付相关的两个页面（2026-10-10 逆向前端 JS 实测确认）

SCAN_PAGE_URL = "https://m.mcd.cn/mcp/scanToPay"
APP_JUMP_URL = "https://m.mcd.cn/mcp/jumpToApp"


def payment_urls(order_id: str,
                 order_category: Optional[str] = None) -> Dict[str, str]:
    """构造支付相关的两个 URL —— **别把 scanToPay 做成二维码**。

    ## 逆向结论（2026-10-10，读 `cdn.mcd.cn/m/build/static/js/main.*.js`）

    前端路由 `/mcp/scanToPay` 对应的组件（压缩后名 `kn`）逻辑是：

        const {search} = useLocation()
        const t = new URLSearchParams(search)
        const n = t.get("orderId") || ""
        const r = t.get("orderCategory") || ""
        let i = `https://${host}/mcp/jumpToApp?orderId=${n}`
        if (r === "ECS") i += `&orderCategory=${r}`
        return <QRCodeSVG value={i}/>          // ← 页面上再画一个二维码

    即 **`/mcp/scanToPay` 本身不是支付页，而是「桌面页」**：
    它读取 orderId，然后在页面里**再渲染一个二维码**，内容指向 `/mcp/jumpToApp`。

    ## 于是有两种正确用法

    | 场景 | 该用哪个 URL |
    |---|---|
    | 用户在**电脑**上，要用**手机扫码** | **`app_jump_url`** ← 做成二维码 |
    | 用户在**手机**上，直接点开 | `app_jump_url`（或 `scan_page_url`，会再引导一次） |

    ⚠️ **踩过的坑**：把 `scan_page_url` 做成二维码给用户手机扫，
    手机上打开的仍是那个「桌面页」，于是又显示一个二维码 ——
    用户看到的就是「一个麦当劳的链接，不是支付」。

    Args:
        order_id: `create-order` 返回的 orderId
        order_category: 仅企业团餐（ECS）需要传 "ECS"；麦乐送/到店不传

    Returns:
        {"scan_page_url": 桌面页, "app_jump_url": 手机深链, "qr_target": 该做成二维码的}
    """
    jump = f"{APP_JUMP_URL}?orderId={order_id}"
    if order_category:
        jump += f"&orderCategory={order_category}"
    scan = f"{SCAN_PAGE_URL}?orderId={order_id}"
    return {
        "scan_page_url": scan,
        "app_jump_url": jump,
        "qr_target": jump,
        "_note": ("扫码用 qr_target（jumpToApp），不要用 scanPageUrl —— "
                  "后者是桌面页，本身会再显示一个二维码。"),
    }


def _print(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    ap = argparse.ArgumentParser(description="本地生成二维码（离线，不走第三方 API）")
    ap.add_argument("content", nargs="?", help="要编码的内容；也可用 --file 读")
    ap.add_argument("--file", type=Path, help="从文件读取内容")
    ap.add_argument("--format", default="svg",
                    choices=["svg", "inline", "png", "pbm", "ascii", "pay-url"])
    ap.add_argument("--out", type=Path, help="输出文件（ascii 格式可省略）")
    ap.add_argument("--scale", type=int, default=8)
    ap.add_argument("--border", type=int, default=4, help="静区格数，QR 规范要求 >=4")
    ap.add_argument("--error", default=DEFAULT_ERROR, choices=["l", "m", "q", "h"])
    ap.add_argument("--invert", action="store_true", help="ascii 反色（浅色终端用）")
    ap.add_argument("--embed", action="store_true",
                    help="输出可直接内联的本机原生路径与 <img> 片段")
    ap.add_argument("--order-id", help="直接给订单号，自动构造正确的支付二维码内容")
    ap.add_argument("--order-category", help="仅企业团餐传 ECS")
    a = ap.parse_args()

    # pay-url 模式：不编码内容，只输出该做成二维码的 URL
    if a.format == "pay-url":
        if not a.order_id:
            raise SystemExit("pay-url 模式需要 --order-id")
        _print(payment_urls(a.order_id, a.order_category))
        return 0

    # --order-id 优先：自动用 jumpToApp 作为二维码内容
    if a.order_id:
        a.content = payment_urls(a.order_id, a.order_category)["qr_target"]

    if a.file:
        content = a.file.read_text(encoding="utf-8").strip()
    elif a.content:
        content = a.content
    else:
        raise SystemExit("请提供内容，或用 --file 指定")

    if a.border < 4:
        print("[warn] 静区小于 4 格可能导致扫码失败", file=sys.stderr)

    if a.format in ("svg", "inline"):
        if a.format == "inline":
            out = to_inline_svg(content, a.error, a.border)
        else:
            out = to_svg(content, a.error, a.scale, a.border)
        if a.out:
            a.out.write_text(out, encoding="utf-8")
            v = verify_inline_svg(content, out, a.error) if a.format == "inline" else None
            info = {"ok": True, "format": a.format, "out": str(a.out),
                    "bytes": len(out.encode())}
            if v:
                info["verified"] = v
            _print(info)
        else:
            _print({"ok": True, "format": a.format, "in_stdout": True,
                    "_note": "SVG 已输出到 stdout，可直接嵌进 HTML/Widget。"})
            print(out)
    elif a.format == "ascii":
        print(to_ascii(content, a.error, min(a.border, 2), a.invert))
    else:
        data = (to_png_bytes(content, a.error, a.scale, a.border)
                if a.format == "png" else to_pbm_bytes(content, a.error, a.border))
        if not a.out:
            a.out = Path(f"qr.{a.format}")
        a.out.write_bytes(data)
        info = {"ok": True, "format": a.format, "out": str(a.out),
                "bytes": len(data)}
        if a.embed:
            # 交回本机原生路径，避免调用方手写 /e/ 这种 Git Bash 风格路径
            info["native_path"] = native_path(a.out)
            info["img_snippet"] = embed_snippet(a.out)
            info["_note"] = ("内联时用 img_snippet 里的路径，不要手写 /e/ 形式；"
                             "或改用 --format svg 内联 SVG，不依赖文件路径。")
        _print(info)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
