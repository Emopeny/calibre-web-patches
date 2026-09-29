# calibre-web 补丁集（构建期打补丁）

把几个真机上踩过的坑固化成**构建期补丁**，叠在 `linuxserver/calibre-web` 镜像上。
不 fork 整个上游项目、不改上游源码树：`Dockerfile` + 4 个补丁脚本就是全部改动，
`docker build` 一次就能复现。

> **非官方项目**：与 calibre-web、linuxserver.io、Calibre 的维护者均无关联。
> 来源与许可见文末「来源与致谢」。

## 修了什么

| # | 症状 | 根因 | 补丁脚本 |
|---|---|---|---|
| 1 | 「获取元数据 → 下载封面」必然失败（`Cover Download Error 418`） | Douban 图床防盗链：无 Referer → **418**，自建站点域名 Referer → **403**；而上游 `save_cover_from_url()` 的两条下载分支都不带 Referer | `patch_cover_referer.py` |
| 2 | 元数据搜索结果的封面在浏览器里加载不出来 | 浏览器直连图片站被防盗链 + CSP `img-src 'self' data:` 双杀，且页面侧无法伪造 Referer → 改为**服务端代理取图** `/metadata/cover?url=...` | `patch_metadata_cover_proxy.py` |
| 3 | 「保存元数据」整个请求 **500** | #2 的初版把 `record["cover"]` **就地改写**成站内相对路径；而同一字段又被 `get_meta.js` 当作表单「Fetch Cover from URL」的值提交 → `requests.InvalidSchema`，且它不在 `save_cover_from_url()` 的 except 名单里 | `patch_metadata_cover_proxy.py`（改成新增 `cover_preview` 字段，`cover` 保持绝对 URL）＋ `patch_cover_url_proxy_save.py`（兜底：拆出内层 URL，并把 `InvalidSchema`/`MissingSchema` 加进 except） |
| 4 | 手机端首屏 89px 布局位移（CLS **0.085**） | `intention.js` 在视口宽度 `<510px` 时把侧栏 `#scnd-nav` 搬进汉堡菜单，侧栏列随后塌陷（90px → 1px）；首帧已经画过，约 250ms 后才塌 → 内容整体上移 89px | `patch_mobile_nav_shift.py` |

补丁 4 的效果（真实浏览器实测，热缓存重复加载）：CLS **0.0846 → 0.0004**，
390 / 600 / 1200px 三档宽度下导航都能点开（**没有**把 510–839px 区间的菜单删掉，见下）。

## 两种用法（互为等价）

- **A. 补丁覆盖层（本仓库）** —— 叠在现成的 `linuxserver/calibre-web` 镜像上构建，
  不改上游源码树；升级 base 只需重新构建，配合 CI 能第一时间发现上游漂移。
- **B. 真 fork（源码里就是改好的）** —— <https://github.com/Emopeny/calibre-web>，
  分支 **`local-patches`**（该 fork 的默认分支），基点上游 tag **0.6.27**，
  修改是 **5 个提交 / 5 个文件**。两边的文件**字节级相同**（git blob 哈希一致）。

想自己改源码、或要基于它打更多补丁 → 走 B；只想尽快跑起来、或想继续用
linuxserver 的镜像（已含 ebook-convert 依赖、s6 等） → 走 A。

## 用法

```bash
git clone https://github.com/Emopeny/calibre-web-patches && cd calibre-web-patches

# 方式 A：直接构建（默认 base = linuxserver/calibre-web:latest）
docker build --pull=false -t calibre-web:patched .

# 方式 B：固定 base 版本，行为可复现（推荐）
docker build --pull=false --build-arg BASE=linuxserver/calibre-web:version-0.6.27 \
             -t calibre-web:patched .
```

⚠️ **`--pull=false` 的理由**：上游 `latest` 一旦升级，下面这些补丁锚点就可能失配。
补丁脚本找不到锚点时会**非 0 退出让构建失败**（有意设计：宁可构建红，
也不要"补丁静默没打上"）。升级 base 请显式做一次，并接受一次真实构建的检验。

启动示例见 `examples/docker-compose.yml`（含只读挂载宿主 calibre 以提供 `ebook-convert`、
日志轮转、healthcheck、内存上限）。

## 每个补丁在改什么

四个脚本都是**纯文本锚点替换**，设计成幂等（已打过 → 退出 0），
锚点缺失 → 非 0 退出。脚本顶部注释写了完整的事故经过与实测数据。

- `patch_cover_referer.py` → `cps/helper.py`
  新增 `_cover_referer_headers(url)` 并按主机分流：`*.douban.com` / `*.doubanio.com`
  用 `https://book.douban.com/`，其它站点用**同源** `scheme://host/`。
  两条下载分支（`cw_advocate.get` 与 `requests.get`）都补上 `headers=`。
  同源是通用解、豆瓣值是白名单语义上的正解 —— 给非豆瓣图床发豆瓣 Referer 反而可能触发
  对方站自己的防盗链。

- `patch_metadata_cover_proxy.py` → `cps/search_metadata.py` + `cps/templates/book_edit.html`
  + `cps/static/js/get_meta.js`
  新增同源路由 `/metadata/cover?url=...`（校验协议、限定 `image/*`、限制 10MB、
  `Cache-Control: public, max-age=86400`），搜索结果的封面改走它。
  **关键语义**：只写 `record["cover_preview"]`，绝不覆盖 `record["cover"]`
  —— 后者同时是保存表单的输入值。

- `patch_cover_url_proxy_save.py` → `cps/helper.py`
  第二道防线：`save_cover_from_url()` 收到 `/metadata/cover?url=<内层绝对 URL>` 这类
  站内相对路径时，拆出内层 URL 继续下载（而不是 500），并把 `InvalidSchema` /
  `MissingSchema` 加进 except 名单。

- `patch_mobile_nav_shift.py` → `cps/static/css/style.css`
  追加一段 `@media (max-width: 509px)` 规则，让侧栏导航在移动端**从一开始就不渲染**
  （与 `intention.js` 搬走后的终态一致）。
  ⚠️ **断点必须是 509px**：它必须对齐上游 `cps/static/js/libs/context.min.js` 里的
  `standard ≥840 / tablet ≥510 / mobile <510`。写成 767/768 会在 510–839px
  区间把导航整个藏掉 —— 那是真回归，不是优化。

## 推送前的隐私闸门

`tests/check-no-private-info.sh` 扫**将要提交的文件**里有没有私人信息（token 前缀、私钥头、
私网网段、邮箱，外加你自己配置的关键词）。关键词**不进仓库**，放两处之一：

```bash
# 1) 环境变量（正则片段，多词用 | 分隔）
BOOKWISH_PRIVACY_PATTERNS='yourdomain\.tld|your-hostname' bash tests/check-no-private-info.sh
# 2) 关键词文件（已被 .gitignore 忽略）
cp tests/.privacy-patterns.example .privacy-patterns && $EDITOR .privacy-patterns
```

退出码非 0 就是命中了，别推。⚠️ **关键词绝不能写进脚本本身** —— 否则闸门自己就把私人信息
带进仓库（这条是真踩过的坑）。

## 已知边界

- 补丁面向 `linuxserver/calibre-web` 的 `/app/calibre-web` 布局；上游换了目录结构或
  改了被锚定的代码，构建会失败（正常，属于预警）。
- 豆瓣侧没有公开 API，防盗链策略随时可能变；本仓库只解决"请求不带 Referer"这一类问题。
- 不包含任何书库数据、配置或站点信息。

## CI

`.github/workflows/verify-patches.yml` 在每次 push/PR（以及手动触发）时：

1. **拉当前 `linuxserver/calibre-web:latest` 全量构建** → 补丁打不上就红；
2. 断言补丁标记真的进了镜像（在构建产物里 grep 各补丁的 marker）;
3. 打印 base 版本与构建出的镜像大小。

第 1 步有意用当前 `latest`：**它是"上游是否已经漂移"的早期预警**。
看到 CI 变红先别急着改补丁，先看是上游改了还是补丁写错了。

## 来源与致谢

本仓库是对下列开源项目的**修改**（modifications），按 GPL-3.0 分发：

- **calibre-web** —— https://github.com/janeczku/calibre-web （GPL-3.0-or-later）
  本补丁集修改的应用本体。上游是 `mutschler/calibreserver` 的 fork。
  被修改的正是它的 `cps/helper.py`、`cps/search_metadata.py`、
  `cps/templates/book_edit.html`、`cps/static/js/get_meta.js`、`cps/static/css/style.css`。
- **linuxserver/docker-calibre-web** —— https://github.com/linuxserver/docker-calibre-web
  本仓库的构建底座（`linuxserver/calibre-web` 镜像，`/app/calibre-web` 布局）。
- **jnozsc/calibre-douban、fugary/calibre-douban** ——
  https://github.com/jnozsc/calibre-douban 、 https://github.com/fugary/calibre-douban
  补丁 1 的 Referer 做法（下载封面时显式带 `Referer: https://book.douban.com/`）
  参考了 calibre-douban 这一可用实现。
- **Calibre** —— https://calibre-ebook.com/ （GPL-3.0）本仓库不包含它，
  示例 compose 里以只读方式挂载宿主已装的 calibre 来提供 `ebook-convert`。

版本对应：本补丁集在 **calibre-web 0.6.27**（linuxserver 构建 `0.6.27-ls402`）上
开发与验证。

## License

GPL-3.0-or-later，见 `LICENSE`（取自上游 calibre-web 的 LICENSE 原文）。
补丁注入的代码会与 calibre-web 一起分发，因此必须沿用同一许可。
