# calibre-web + ebook-convert 运行依赖
#
# 基础: linuxserver/calibre-web (Ubuntu 24.04)
# calibre 本体**不在**镜像内 —— 通过只读挂载复用主机 /opt/calibre
# (与已停用的 calibre-server 同一份安装, calibre 9.15; 避免重复下载 634MB)
#
# 本层只补 calibre 转换 (ebook-convert) 时需要的 Qt/GL 系统库。
# 官方镜像未装这些库, 缺了会报:
#   ImportError: cannot import name 'QColor' from 'qt.core'
#
# 重建 (务必带 --pull=false):
#   docker build --pull=false -t calibre-web:local /opt/calibre-web-image/
#   --pull=false 让 build 复用本地缓存的 base 镜像: 本机直连 registry 不稳,
#   而且**上游静默升级会让下面的补丁锚点失配** —— 要升 base 就显式操作一次。
#
# base 可以用 --build-arg 固定到具体版本, 便于复现与排查:
#   docker build --pull=false --build-arg BASE=linuxserver/calibre-web:version-0.6.27 -t calibre-web:local .
ARG BASE=linuxserver/calibre-web:latest
FROM ${BASE}

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libegl1 \
      libglx0 \
      libopengl0 \
      libxkbcommon0 \
      libgl1 \
      libdbus-1-3 \
      libfontconfig1 \
      libxcb1 \
      libx11-xcb1 \
      libxcb-cursor0 \
 && rm -rf /var/lib/apt/lists/*

# ── 修复 1: 「获取元数据 → 下载封面」418 ─────────────────────────────────────
# Douban 图床 (img*.doubanio.com) 有防盗链: 无 Referer 的请求返回
#   418 Client Error: I'm a Teapot
# 上游 cps/helper.py 的 save_cover_from_url() 两条分支都不带 Referer。
# 此步骤补一个同源 Referer。
COPY patch_cover_referer.py patch_cover_url_proxy_save.py /tmp/

RUN python3 /tmp/patch_cover_referer.py /app/calibre-web/cps/helper.py \
 && python3 /tmp/patch_cover_url_proxy_save.py /app/calibre-web/cps/helper.py \
 && rm -f /tmp/patch_cover_referer.py /tmp/patch_cover_url_proxy_save.py

# ── 修复 2: 元数据预览封面 (代理取图) + 「保存元数据」500 ────────────────────
# 预览: 搜索结果封面由浏览器直连图片站, 而 Douban 图床只认自家 Referer
#   (Referer=<你的站点域名> -> 403; 无 Referer -> 418; CSP img-src 'self' 也挡),
#   改为服务端代理取图 /metadata/cover?url=...
# 保存: 代理初版把搜索结果里的 cover 就地改写成站内相对路径, 而该字段同时是
#   book_edit 表单「Fetch Cover from URL」的值 -> save_cover_from_url() 收到相对
#   URL 抛 InvalidSchema: No connection adapters were found -> 保存 500。
#   本版改为新增 cover_preview 字段供显示, cover 保持绝对 URL; 消费方
#   (book_edit.html / get_meta.js) 一并改。详见脚本顶部说明。
# 补丁脚本在找不到目标代码时会以非 0 退出, 从而让镜像构建直接失败 ——
# 避免上游改版后补丁静默失效。
COPY patch_metadata_cover_proxy.py /tmp/patch_metadata_cover_proxy.py
RUN python3 /tmp/patch_metadata_cover_proxy.py /app/calibre-web \
 && rm -f /tmp/patch_metadata_cover_proxy.py

# ── 修复 3: 移动端侧栏塌陷造成的首屏 89px 位移（CLS 0.085 → 0） ──────────────
# intention.js 在视口 <510px 时把 #scnd-nav 搬进汉堡菜单，侧栏列随之塌陷 →
# 首屏绘制后再位移（手机热缓存实测 CLS 0.0846）。补一段同断点的 CSS，让它从一开始
# 就不渲染（与搬走后的最终状态一致）。
# ⚠️ 断点必须 509px —— 与 cps/static/js/libs/context.min.js 的 mobile 上下文一致；
#    写成 767/768 会让 510–839px 区间（导航本应留在侧栏）的菜单消失。详见脚本顶部说明。
COPY patch_mobile_nav_shift.py /tmp/
RUN python3 /tmp/patch_mobile_nav_shift.py /app/calibre-web/cps/static/css/style.css \
 && rm -f /tmp/patch_mobile_nav_shift.py
