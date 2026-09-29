#!/usr/bin/env python3
"""修复 3: 移动端首屏 89px 位移（CLS 0.085）—— 往 cps/static/css/style.css 追加一段规则。

问题（实测，非推断）
--------------------
layout.html 的侧栏是
    <div class="col-sm-2"><nav class="navigation">
      <ul id="scnd-nav" intent in-standard-append="nav.navigation"
          in-mobile-after="#main-nav" in-mobile-class="nav navbar-nav">...
由 intention.js 按视口宽度搬位置：standard(≥840) 原地、mobile(<510) 搬进汉堡菜单。
而 <510px 时搬走之后，侧栏列必然**空掉**（实测高度 90px → 1px），
但首次绘制时它还在、JS 跑完（约 250-300ms）才塌 —— 于是

    手机端热缓存重复加载: CLS 0.0846，layout-shift 0.0846 一次
    （实测源元素: div.col-sm-10 从 y=249 → y=160，整页上移 89px）

修法
----
在**同一个断点**下让侧栏导航从一开始就不渲染。搬走后的最终状态本来就是"侧栏里没有它"，
所以视觉终态完全一致；只是把"先画后塌"变成"从一开始就不画"。

⚠️ 断点必须是 509px，不是 767/768：
   intention 的宽度上下文在 cps/static/js/libs/context.min.js 里写死
       standard: min 840 / tablet: min 510 / mobile: min 0   （量的是 $(window).width()）
   即只有 **视口 < 510px** 才会触发 in-mobile-after 的搬移。
   510–839px（tablet）区间导航**留在侧栏里正常显示** —— 若把断点写成 767px，
   会在这一区间把导航整个藏掉（菜单丢失的真回归）。三档宽度已实测覆盖。

用法（构建期调用，见同目录 Dockerfile）
    python3 patch_mobile_nav_shift.py /app/calibre-web/cps/static/css/style.css
退出码: 0 已打补丁或本来就是补丁态 / 2 目标文件不存在或不含预期结构（让构建失败，别静默失效）
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* [local patch] 移动端侧栏位移修复"
ANCHOR = ".navigation"             # 上游样式里必须有导航样式，否则说明文件换了/结构变了
BLOCK = """

/* [local patch] 移动端侧栏位移修复 (2026-09-29)
   intention.js 在视口 <510px 时把 #scnd-nav 搬进汉堡菜单，侧栏列随即塌陷 89px，
   造成首屏布局位移（手机热缓存实测 CLS 0.0846）。同一断点下从一开始就不渲染它，
   与搬走后的最终状态一致。
   ⚠️ 断点 509px 必须与 cps/static/js/libs/context.min.js 的 mobile 上下文一致
      （standard 840 / tablet 510 / mobile <510）。写成 767/768 会让
      510–839px 区间（导航本应留在侧栏）的菜单消失。 */
@media (max-width: 509px) {
  .row-fluid > .col-sm-2 > nav.navigation { display: none; }
}
"""


def patch(path: Path) -> int:
    if not path.is_file():
        print(f"[patch] 目标文件不存在: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print(f"[patch] already applied: {path}")
        return 0
    if ANCHOR not in text:
        print(f"[patch] {path} 里找不到锚点 {ANCHOR!r} —— 上游样式结构变了，"
              f"请人工确认后再改本补丁（**不要**盲目去掉锚点检查）", file=sys.stderr)
        return 2
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text + BLOCK, encoding="utf-8")
    print(f"[patch] applied to {path} (+{len(BLOCK)} 字节)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        print("usage: patch_mobile_nav_shift.py <path to cps/static/css/style.css>", file=sys.stderr)
        sys.exit(2)
    sys.exit(patch(Path(sys.argv[1])))
