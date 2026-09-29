#!/usr/bin/env python3
"""让 calibre-web 的「元数据预览封面」走服务端代理, 且**不污染保存用的 cover URL**。

问题 1 —— 预览图加载失败 (本脚本第 1 部分, 初版已修)
----------------------------------------------------
元数据搜索结果里的封面是浏览器**直连**图片站的:
    cps/templates/book_edit.html:  <img src="<%- cover %>">
而 Douban 图床只认 douban 自家的 Referer, 于是浏览器带上的
    Referer: <你的站点域名>/...                -> 403
被拒。实测三种情形:
    Referer=<你的站点域名>        -> 403   (预览图加载失败, 用户看到的现象)
    Referer=book.douban.com      -> 200   (服务端保存封面走的就是这条, 所以能成功)
    无 Referer                   -> 418   (所以 Referrer-Policy: no-referrer 之类的方案无效)
浏览器里无法伪造 Referer, 只能改由服务端代取。另有 CSP `img-src 'self' data:`
(见任意静态资源响应头) 也一并挡死直连, 双保险。

问题 2 —— 保存元数据 500 ★本版修复★
------------------------------------
初版(2026-09-27)把 record["cover"] **就地改写**成 /metadata/cover?url=... ,
但 cover 这个字段同时被 cps/static/js/get_meta.js 当作「Fetch Cover from URL」表单值:
    $("#cover_url").val(book.cover);          // get_meta.js populateForm()
提交后 cps/editbooks.py 调 helper.save_cover_from_url(该值), requests 收到
站内相对路径 -> requests.exceptions.InvalidSchema:
    No connection adapters were found for
    '/metadata/cover?url=https://img9.doubanio.com/view/subject/l/public/s28299076.jpg'
并且 InvalidSchema 不在 save_cover_from_url 的 except 名单里 -> 整个保存请求 500
(线上复现: 应用日志 /config/calibre-web.log 里的 No connection adapters were found)。

修法: **不再覆盖 cover**, 改为新增同源字段 cover_preview 专供显示:
    record["cover"]         -> 保持原样 (绝对 http(s) URL, 供表单保存)
    record["cover_preview"] -> url_for("metadata.metadata_cover", url=...) (供 <img src>)
两个消费方跟着改:
    cps/templates/book_edit.html  搜索结果列表缩略图   -> cover_preview || cover
    cps/static/js/get_meta.js     编辑页表单缩略图     -> book.cover_preview || book.cover
(get_meta.js 里 $("#cover_url").val(book.cover) **保持不改** —— 它要的就是绝对 URL。
 该文件响应头是 cache-control: no-cache, 浏览器会回源校验, 改动即刻生效;
 万一仍有旧 JS 缓存, cps/helper.py 侧还有 patch_cover_url_proxy_save.py 兜底。)

用法
----
    python3 patch_metadata_cover_proxy.py /app/calibre-web

幂等: 三个文件都已打补丁 -> 退出 0。
安全: 任一锚点找不到 -> 退出 1, 让镜像构建失败 (上游改版必须暴露, 不能静默不补)。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 统一标记子串 (用于幂等判断); 各文件按自己的注释语法写
MARKER = "metadata-cover-proxy-patch"
PY_MARKER = "# <" + MARKER + ">"
HTML_MARKER = "<!-- <" + MARKER + "> -->"
JS_MARKER = "// <" + MARKER + ">"

PY_BLOCK = '''

# <metadata-cover-proxy-patch>
# 元数据预览封面代理: 浏览器直连图片站会被防盗链 + CSP 拦 (Douban 只认自家 Referer),
# 因此改由服务端代取。见 patch_metadata_cover_proxy.py 顶部说明。
MAX_PREVIEW_COVER_BYTES = 10 * 1024 * 1024


def _preview_cover_headers(url):
    """与 cps.helper 中同一套 Referer 规则, 复用避免两处逻辑漂移。"""
    try:
        from .helper import _cover_referer_headers
        headers = _cover_referer_headers(url)
        if headers:
            return headers
    except Exception:
        pass
    # helper 补丁缺失时的兜底, 规则与之一致
    try:
        parts = urlsplit(url)
        host = (parts.netloc or "").lower()
        if parts.scheme and host:
            if host.endswith("doubanio.com") or host.endswith("douban.com"):
                return {"Referer": "https://book.douban.com/"}
            return {"Referer": "{0}://{1}/".format(parts.scheme, parts.netloc)}
    except Exception:
        pass
    return {}


def _fetch_preview_cover(url):
    """服务端取图。优先走 cw_advocate 以复用其 SSRF 防护。"""
    headers = _preview_cover_headers(url)
    try:
        from . import cw_advocate
        return cw_advocate.get(url, timeout=(10, 20), allow_redirects=False, headers=headers)
    except ImportError:
        import requests
        return requests.get(url, timeout=(10, 20), allow_redirects=False, headers=headers)


@meta.route("/metadata/cover")
@user_login_required
def metadata_cover():
    url = _safe_metadata_url(request.args.get("url", ""))
    if not url.startswith(("http://", "https://")):
        return make_response("", 400)
    try:
        upstream = _fetch_preview_cover(url)
    except Exception as exc:
        log.debug("preview cover fetch failed for %s: %s", url, exc)
        return make_response("", 502)
    if upstream.status_code != 200:
        log.debug("preview cover upstream %s for %s", upstream.status_code, url)
        return make_response("", 502)
    content_type = (upstream.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if not content_type.startswith("image/"):
        return make_response("", 415)
    body = upstream.content
    if len(body) > MAX_PREVIEW_COVER_BYTES:
        return make_response("", 413)
    response = make_response(body)
    response.headers["Content-Type"] = content_type
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response
'''

PY_ANCHOR = "log = logger.create()\n"

PY_OLD_COVER = '    record["cover"] = _safe_metadata_url(record.get("cover"))\n'

PY_NEW_COVER = (
    '    record["cover"] = _safe_metadata_url(record.get("cover"))\n'
    '    # <' + MARKER + '>\n'
    '    # ★ 只写 cover_preview, 绝不覆盖 cover: cover 同时是 book_edit 表单\n'
    '    #   「Fetch Cover from URL」的值 (get_meta.js populateForm), 覆盖它会把\n'
    '    #   站内相对路径送进 helper.save_cover_from_url() -> InvalidSchema 500\n'
    '    #   (2026-09-28 事故: calibre-web.log 里 "No connection adapters were found")。\n'
    '    record["cover_preview"] = record["cover"]\n'
    '    if record["cover"].startswith(("http://", "https://")):\n'
    '        record["cover_preview"] = url_for("metadata.metadata_cover", url=record["cover"])\n'
)

HTML_ANCHOR = '<script type="text/template" id="template-book-result">\n'
HTML_NEW = (
    HTML_ANCHOR
    + "  " + HTML_MARKER + "\n"
    + "  <!-- 缩略图走服务端代理 (cover_preview); data.cover 保持绝对 URL, 供表单保存。-->\n"
)

HTML_OLD_IMG = 'src="<%- cover || '
HTML_NEW_IMG = 'src="<%- cover_preview || cover || '

JS_OLD_LINE = '            $(".cover img").attr("src", book.cover);\n'
JS_NEW_LINE = (
    '            ' + JS_MARKER + '\n'
    '            // 预览用同源代理; 表单 cover_url 仍旧是绝对 URL (下一行)。\n'
    '            $(".cover img").attr("src", book.cover_preview || book.cover);\n'
)


def patch_search_metadata(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if 'record["cover_preview"]' in text:
        return "already patched (v2)"
    if MARKER in text:
        return "FAIL: v1 版本残留 (cover 被就地改写), 需从上游镜像重建"
    if text.count(PY_ANCHOR) != 1:
        return "FAIL: expected exactly one %r anchor, found %d" % (PY_ANCHOR, text.count(PY_ANCHOR))
    if text.count(PY_OLD_COVER) != 1:
        return "FAIL: cover assignment anchor not found exactly once (%d)" % text.count(PY_OLD_COVER)
    if "def _safe_metadata_url" not in text or "from urllib.parse import urlsplit" not in text:
        return "FAIL: prerequisites missing (_safe_metadata_url / urlsplit)"
    text = text.replace(PY_ANCHOR, PY_ANCHOR + PY_BLOCK, 1)
    text = text.replace(PY_OLD_COVER, PY_NEW_COVER, 1)
    if text.count(PY_MARKER) != 2:
        return "FAIL: unexpected marker count after patch (%d)" % text.count(PY_MARKER)
    path.write_text(text, encoding="utf-8")
    return "ok: proxy route + cover_preview (cover 保持绝对 URL)"


def patch_template(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if HTML_MARKER in text:
        return "already patched"
    if text.count(HTML_ANCHOR) != 1:
        return "FAIL: template-book-result anchor not found exactly once (%d)" % text.count(HTML_ANCHOR)
    if text.count(HTML_OLD_IMG) != 1:
        return "FAIL: thumbnail img src anchor not found exactly once (%d)" % text.count(HTML_OLD_IMG)
    text = text.replace(HTML_ANCHOR, HTML_NEW, 1)
    text = text.replace(HTML_OLD_IMG, HTML_NEW_IMG, 1)
    if text.count(HTML_MARKER) != 1 or text.count('cover_preview || cover') != 1:
        return "FAIL: unexpected result after patch"
    path.write_text(text, encoding="utf-8")
    return "ok: 列表缩略图改用 cover_preview"


def patch_js(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if JS_MARKER in text:
        return "already patched"
    if text.count(JS_OLD_LINE) != 1:
        return "FAIL: $(\".cover img\") anchor not found exactly once (%d)" % text.count(JS_OLD_LINE)
    if '$("#cover_url").val(book.cover);' not in text:
        return "FAIL: #cover_url 赋值缺失 —— 语义前提变了, 请人工复核"
    text = text.replace(JS_OLD_LINE, JS_NEW_LINE, 1)
    if text.count(JS_MARKER) != 1:
        return "FAIL: unexpected marker count after patch"
    path.write_text(text, encoding="utf-8")
    return "ok: 编辑页表单缩略图改用 cover_preview (cover_url 仍为绝对 URL)"


TARGETS = (
    ("cps/search_metadata.py", patch_search_metadata),
    ("cps/templates/book_edit.html", patch_template),
    ("cps/static/js/get_meta.js", patch_js),
)


def patch(app_root: Path) -> int:
    failed = []
    for rel, fn in TARGETS:
        target = app_root / rel
        if not target.is_file():
            print("[patch] FAIL: %s not found" % target, file=sys.stderr)
            failed.append(rel)
            continue
        result = fn(target)
        print("[patch] %-28s %s" % (rel, result))
        if result.startswith("FAIL"):
            failed.append(rel)
    if failed:
        print("[patch] build must fail, unpatched: %s" % ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: patch_metadata_cover_proxy.py <calibre-web app root>", file=sys.stderr)
        sys.exit(2)
    sys.exit(patch(Path(sys.argv[1])))
