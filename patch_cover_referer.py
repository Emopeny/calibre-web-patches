#!/usr/bin/env python3
"""给 calibre-web 的「取封面」请求补上 Referer.

问题
----
Douban 图床 (img*.doubanio.com) 有防盗链: 请求不带 Referer 直接返回
    418 Client Error: I'm a Teapot
于是 calibre-web 的「获取元数据 → 下载封面」必然失败, 日志里是
    ERROR {helper.py:845} Cover Download Error 418 ... 

上游 cps/helper.py 的 save_cover_from_url() 两条分支都没有带 Referer。

修法 (对齐 calibre-douban 这个可用的参考实现)
------------------------------------------------
calibre-douban 在下载封面时显式设置:
    br.set_current_header('Referer', 'https://book.douban.com/')
即使用**站点自身**的 Referer, 而不是图片主机自身。

本补丁按主机分流:
  * doubanio.com / douban.com  -> Referer: https://book.douban.com/
  * 其它来源                    -> Referer: 同源 (scheme://host/)
分流而不是一律硬编码 douban, 是因为 Referer 对**所有**站点都会发出去:
给非豆瓣图床发一个豆瓣 Referer, 可能反而触发那个站自己的防盗链。
同源值是通用安全解, 豆瓣站点值是白名单语义上的正解。

用法
----
    python3 patch_cover_referer.py /app/calibre-web/cps/helper.py

幂等: 已打过补丁 -> 退出 0, 不重复改。
安全: 找不到预期代码 -> 退出 1。上游一改版就必须让镜像构建失败,
      而不是静默跳过 —— 「补丁没打上」比「构建失败」危险得多。
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "# <cover-referer-patch>"

HELPER_FN = '''def _cover_referer_headers(url):
    """按主机给出 Referer, 绕过 Douban 图床等站点的防盗链 (无 Referer 会 418)。
    {marker}"""
    try:
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        host = (parts.netloc or "").lower()
        if parts.scheme and host:
            if host.endswith("doubanio.com") or host.endswith("douban.com"):
                return {{"Referer": "https://book.douban.com/"}}
            return {{"Referer": "{{0}}://{{1}}/".format(parts.scheme, parts.netloc)}}
    except Exception:
        pass
    return {{}}


'''.format(marker=MARKER)

ANCHOR = "# saves book cover from url"

# 两条分支都要补: 运行时走 cw_advocate, 但 allow_localhost 打开时走 requests。
CALL_SITES = (
    "requests.get(url, timeout=(10, 200), allow_redirects=False)",
    "cw_advocate.get(url, timeout=(10, 200), allow_redirects=False)",
)


def patch(path: Path) -> int:
    text = path.read_text(encoding="utf-8")

    if MARKER in text:
        print(f"[patch] already applied: {path}")
        return 0

    if ANCHOR not in text:
        print(f"[patch] FAIL: anchor not found ({ANCHOR!r}); upstream layout changed", file=sys.stderr)
        return 1

    changed = 0
    for call in CALL_SITES:
        if call not in text:
            print(f"[patch] FAIL: call site not found ({call!r})", file=sys.stderr)
            return 1
        text = text.replace(call, f"{call[:-1]}, headers=_cover_referer_headers(url))", 1)
        changed += 1

    text = text.replace(ANCHOR, HELPER_FN + ANCHOR, 1)

    if text.count("_cover_referer_headers(url)") != len(CALL_SITES) + 1:
        print("[patch] FAIL: unexpected replacement count", file=sys.stderr)
        return 1

    path.write_text(text, encoding="utf-8")
    print(f"[patch] applied to {path} ({changed} call site(s) + helper)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: patch_cover_referer.py <path to cps/helper.py>", file=sys.stderr)
        sys.exit(2)
    sys.exit(patch(Path(sys.argv[1])))
