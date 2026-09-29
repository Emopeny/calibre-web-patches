#!/usr/bin/env python3
"""兜底: 「Fetch Cover from URL」收到站内相对路径时, 别 500, 拆出内层真实 URL。

背景 (2026-09-28 事故)
----------------------
元数据预览封面代理 (patch_metadata_cover_proxy.py) 初版把搜索结果里的
record["cover"] 就地改写成站内相对路径 /metadata/cover?url=<真实图片 URL>。
而 cps/static/js/get_meta.js 用同一个字段填表单:

    $("#cover_url").val(book.cover);        // populateForm()

提交后 cps/editbooks.py 调 helper.save_cover_from_url(该值) -> requests 收到
相对 URL 直接抛:

    requests.exceptions.InvalidSchema:
    No connection adapters were found for
    '/metadata/cover?url=https://img9.doubanio.com/view/subject/l/public/s28299076.jpg'

InvalidSchema 不在 save_cover_from_url 的 except 名单里 -> 保存元数据整整 500。

一手修复是改字段语义 (cover_preview), 见 patch_metadata_cover_proxy.py。
本脚本是**第二道防线**, 覆盖两种残余情形:
  1. 浏览器里仍有缓存/书签/旧页面携带的 /metadata/cover?url=... 值;
  2. 用户手工往输入框里粘了这类站内地址。
处理: 拆出内层绝对 URL 继续下载; 拆不出来则走 except 分支给友好提示
(而不是 500), 因此同时把 requests.exceptions.InvalidSchema 加进 except 名单。

用法
----
    python3 patch_cover_url_proxy_save.py /app/calibre-web/cps/helper.py

幂等: 已打过 -> 退出 0。
安全: 锚点找不到 -> 退出 1, 让镜像构建失败。
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "# <cover-url-proxy-save-patch>"

BLOCK = '''

# <cover-url-proxy-save-patch>
# 站内封面代理地址 -> 内层绝对 URL。见 patch_cover_url_proxy_save.py 顶部说明。
def _unwrap_local_cover_proxy_url(url):
    if not isinstance(url, str):
        return url
    candidate = url.strip()
    try:
        from urllib.parse import urlsplit, parse_qs, unquote

        parts = urlsplit(candidate)
        if parts.scheme or parts.netloc:
            return candidate
        if parts.path.rstrip("/") != "/metadata/cover":
            return candidate
        query = parts.query
        inner = ""
        if query.startswith("url="):
            # 不截断: 内层 URL 自带 & 参数时, parse_qs 会把尾巴吃掉
            inner = unquote(query[4:]).strip()
        else:
            inner = parse_qs(query).get("url", [""])[0].strip()
        if inner.startswith(("http://", "https://")):
            log.warning("cover_url was a local preview-proxy path, using inner url: %s", inner)
            return inner
    except Exception as exc:
        log.debug("could not unwrap local cover proxy url %r: %s", candidate, exc)
    return candidate
'''

ANCHOR = "\n# saves book cover from url\ndef save_cover_from_url(url, book_path):\n    try:\n"

NEW_ANCHOR = (
    BLOCK
    + "\n# saves book cover from url\ndef save_cover_from_url(url, book_path):\n    try:\n"
    + "        url = _unwrap_local_cover_proxy_url(url)\n"
)

OLD_EXCEPT = """    except (socket.gaierror,
            requests.exceptions.HTTPError,
            requests.exceptions.InvalidURL,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout) as ex:
"""
NEW_EXCEPT = """    except (socket.gaierror,
            requests.exceptions.HTTPError,
            requests.exceptions.InvalidURL,
            # 相对/畸形 URL 走的是这两个异常 (实测): '/metadata/cover?url=..' -> InvalidSchema,
            # '/metadata/cover' -> MissingSchema; 上游都没 catch, 会让整个保存请求 500
            requests.exceptions.InvalidSchema,
            requests.exceptions.MissingSchema,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout) as ex:
"""


def patch(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print("[patch] already applied: %s" % path)
        return 0
    if text.count(ANCHOR) != 1:
        print("[patch] FAIL: save_cover_from_url anchor not found exactly once (%d)" % text.count(ANCHOR),
              file=sys.stderr)
        return 1
    if text.count(OLD_EXCEPT) != 1:
        print("[patch] FAIL: except-tuple anchor not found exactly once (%d)" % text.count(OLD_EXCEPT),
              file=sys.stderr)
        return 1
    text = text.replace(ANCHOR, NEW_ANCHOR, 1)
    text = text.replace(OLD_EXCEPT, NEW_EXCEPT, 1)
    if text.count(MARKER) != 1 or "_unwrap_local_cover_proxy_url(url)" not in text:
        print("[patch] FAIL: unexpected result after patch", file=sys.stderr)
        return 1
    path.write_text(text, encoding="utf-8")
    print("[patch] applied to %s (unwrap local proxy url + catch InvalidSchema)" % path)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: patch_cover_url_proxy_save.py <path to cps/helper.py>", file=sys.stderr)
        sys.exit(2)
    sys.exit(patch(Path(sys.argv[1])))
