# JavDB 私有 API 文档

> 面向 AI / 开发者的可直接调用参考。**不要爬 `javdb.com` 网页**（有 Cloudflare 盾），一律走 App 后端 API。
>
> 本文档所有端点均于 2026-06 实测通过。签名密钥为逆向 App 所得，JavDB 更换后会全线失效，届时需重新抓包。

---

## 1. 一句话结论

| 你要的东西 | 怎么拿 |
|---|---|
| 按番号搜影片 | `GET /v2/search?q=ABP-001&type=movie` |
| 影片完整元数据（**标签、演员**、导演、片商、系列、评分…） | `GET /v4/movies/{id}` |
| 评论 | `GET /v1/movies/{id}/reviews` |
| 磁力链接 | `GET /v1/movies/{id}/magnets` |
| 演员资料 + 标签统计 | `GET /v1/actors/{id}` |
| 标签字典（全部分类） | `GET /v1/tags?type=0` |

**注意**：搜索接口只返回列表摘要，**不含 tags/actors**。要标签和演员必须再调 `/v4/movies/{id}`。

---

## 2. 鉴权：`jdsignature` 签名

每个请求都必须带签名头，否则拒绝。

### 算法

```
signature = "{unix_seconds}.lpw6vgqzsp.{md5_hex}"
md5_hex   = MD5( unix_seconds字符串 + SECRET )   // 输出小写 hex
```

- `unix_seconds`：当前秒级时间戳（**不是毫秒**）
- 固定串：`lpw6vgqzsp`
- `SECRET`（64 字节 hex 串，原文粘贴，勿改动）：

```
71cf27bb3c0bcdf207b64abecddc970098c7421ee7203b9cdae54478478a199e7d5a6e1a57691123c1a931c057842fb73ba3b3c83bcd69c17ccf174081e3d8aa
```

### 参考实现

**Python**

```python
import hashlib, time

SECRET = "71cf27bb3c0bcdf207b64abecddc970098c7421ee7203b9cdae54478478a199e7d5a6e1a57691123c1a931c057842fb73ba3b3c83bcd69c17ccf174081e3d8aa"

def build_signature() -> str:
    ts = str(int(time.time()))
    digest = hashlib.md5((ts + SECRET).encode()).hexdigest()
    return f"{ts}.lpw6vgqzsp.{digest}"
```

**JavaScript（无依赖，含 MD5）**

见本仓库 `emby-javdb-comments.user.js` 的 `md5()` + `buildSignature()`（约 74–213 行），可直接复制。

### 缓存

服务端按时间戳容忍约 **300 秒**，签名可缓存 5 分钟复用。超时后重新生成。

### 请求头名

| 场景 | 头名 | 说明 |
|---|---|---|
| 搜索 `/v2/search`、登录 `/v1/sessions`、标签 `/v1/tags` | `jdsignature` | 小写 |
| 其余所有端点 | `jdSignature` | 驼峰 |

实测服务端大小写不敏感，但按上表传最稳妥。**两个头可以同时带上**，一劳永逸。

---

## 3. 通用约定

### Base URL

```
https://jdforrepam.com/api
```

### 必带请求头

```
user-agent: Dart/3.5 (dart:io)      # 伪装 Flutter App，必须
accept-language: zh-TW
jdsignature: <签名>
jdSignature: <签名>
```

### 响应外壳

成功：

```json
{ "success": 1, "action": null, "message": null, "data": { ... } }
```

失败：

```json
{ "success": 0, "action": "ParameterInvalid", "message": "參數不能爲空: q", "data": null }
```

常见 `action`：

| action | message | 含义 |
|---|---|---|
| `ParameterInvalid` | `參數不能爲空: xxx` | 缺参数 |
| `NonExistentUser` | `帳號不存在` | 登录账号错 |
| `JWTVerificationError` | `請登錄帳號` | 该接口需要 Bearer 登录态 |

HTTP 层面：不存在的路径返回 **404 HTML**（不是 JSON），参数非法但路由正确返回 **200 + 上面的 JSON 失败壳**。

### 分页

列表类参数：`page`（从 1 开始）、`limit`。
响应里的 `current_page` 表示当前页；**没有 `total_pages` / `total` 字段**，翻页需靠「本页条数 < limit」判断到底。

### 图片 URL 重写

返回的图片域名是负载均衡占位，需替换为官方 CDN：

```python
import re
def fix_img(url: str) -> str:
    return re.sub(r"https://.*?/rhe951l4q", "https://c0.jdbstatic.com", url or "")
```

原文路径形如 `https://tp.spfcas.com/rhe951l4q/covers/e2/E2630.jpg` → `https://c0.jdbstatic.com/covers/e2/E2630.jpg`。
不重写也能用，但 `tp.spfcas.com` 可能不稳定。

### 磁力 `size` 单位

**MB**。换算 GB：`size / 1024`。

---

## 4. 端点详情

### 4.1 搜索 — `GET /v2/search`

**参数**

| 参数 | 必填 | 默认/示例 | 说明 |
|---|---|---|---|
| `q` | ✅ | `ABP-001` | 关键词，**不可为空** |
| `type` | | `movie` | 见下表 |
| `page` | | `1` | |
| `limit` | | `20` | |
| `movie_type` | | `all` | `all` / 有码无码等 |
| `from_recent` | | `false` | |
| `movie_filter_by` | | `all` | |
| `movie_sort_by` | | `relevance` | 也可 `release_date` 等 |

**`type` 取值 → 响应 `data` 里的键**

| type | data 键 | 条目字段 |
|---|---|---|
| `movie` | `movies[]` + `current_page` | 见下 |
| `actor` | `actors[]` | `id, type, avatar_url, name, name_zht, other_name, uncensored, gender, videos_count` |
| `series` | `series[]` | `id, name, type, videos_count` |
| `maker` | `makers[]` | `id, name, type, videos_count` |
| `director` | `directors[]` | 同上（样本里常为空） |
| `code` | `codes[]` | `id`(番号前缀如 `ABP`), `name, type, videos_count` |
| `list` | `lists[]` | `id, name, privacy, is_default, movies_count, has_movie` |
| `star` / `tag` | `movies[]` | 同 `movie`（按演员名/标签名搜片） |

**`movies[]` 条目字段**（列表摘要，**无 tags/actors**）：

```json
{
  "id": "E2630",
  "number": "ABP-001",
  "title": "水咲ローラがご奉仕しちゃう超最新やみつきエステ",
  "origin_title": "水咲ローラがご奉仕しちゃう超最新やみつきエステ",
  "thumb_url": "https://tp.spfcas.com/rhe951l4q/small_covers/e2/E2630.jpg",
  "cover_url": "https://tp.spfcas.com/rhe951l4q/covers/e2/E2630.jpg",
  "duration": 120,
  "magnets_count": 33,
  "can_play": true,
  "play_subtitle": 1,
  "has_preview_video": true,
  "has_cnsub": true,
  "has_preview_images": true,
  "release_date": "2013-06-01",
  "new_magnets": false,
  "first_magnets": null,
  "preview_images": []
}
```

**示例**

```
GET /v2/search?q=ABP-001&type=movie&page=1&limit=5&movie_type=all&from_recent=false&movie_filter_by=all&movie_sort_by=relevance
```

---

### 4.2 影片详情 — `GET /v4/movies/{id}`

**唯一能拿到标签、演员、导演、片商、系列的地方。**

路径里的 `{id}` 是 JavDB 内部短 ID（如 `E2630`），**不是番号**。先用搜索拿到 `id`。

**`data.movie` 字段全表**

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 内部 ID，后续接口用它 |
| `number` | string | 番号，如 `ABP-001` |
| `number_letter` | string | 番号前缀，如 `ABP` |
| `title` / `origin_title` | string | 中文译名 / 原名 |
| `summary` | string\|null | 简介 |
| `type` | int | 0=有码类 |
| `score` | string | 评分，如 `"4.39"` |
| `reviews_count` / `comments_count` | int | 长评数 / 短评数 |
| `want_watch_count` / `watched_count` | int | 想看 / 看过 |
| `duration` | int | 分钟 |
| `release_date` | string | `YYYY-MM-DD` |
| `magnets_count` | int | 磁力数 |
| `has_cnsub` / `has_preview_images` / `has_preview_video` / `can_play` | bool | |
| `play_subtitle` | int | |
| `play_sources` | array | `[{id, name}]` |
| `thumb_url` / `cover_url` | string | 缩略图 / 封面（需重写） |
| `preview_images` | array | `[{large_url, ...}]`，需重写 |
| `preview_video_url` | string | 预告片 m3u8，自带 sign |
| **`tags`** | **array** | **`[{id, name}]` ← 标签** |
| **`actors`** | **array** | **`[{id, name, gender, avatar_url}]` ← 演员** |
| `director_id` / `director_name` | string\|null | 导演 |
| `maker_id` / `maker_name` | string\|null | 片商 |
| `publisher_id` / `publisher_name` | string\|null | 发行商 |
| `series_id` / `series_name` | string\|null | 系列 |
| `relative_movies` | array | `[{id, number, thumb_url}]` 相关作品 |
| `actor_movies` | array | 同上，同演员其他作品（部分） |
| `top_rankings` | array | `[{ranking, title, top_type, movie_type, year}]` |
| `review` | object\|null | 当前用户自己的评价（未登录为 null） |

**`actors[]` 的 `gender`**：`0` = 女优，`1` = 男优。

**示例响应片段**

```json
{
  "tags": [
    { "id": "28", "name": "单体作品" },
    { "id": "70", "name": "妓女" },
    { "id": "102", "name": "手淫" }
  ],
  "actors": [
    { "id": "OpzD", "name": "水咲ローラ", "gender": 0, "avatar_url": "https://tp.spfcas.com/rhe951l4q/avatars/op/OpzD.jpg" },
    { "id": "x7wn", "name": "田淵正浩", "gender": 1, "avatar_url": "https://tp.spfcas.com/rhe951l4q/avatars/x7/x7wn.jpg" }
  ],
  "director_id": null,
  "director_name": null,
  "maker_id": "6M",
  "maker_name": "プレステージ",
  "publisher_id": "XG4",
  "publisher_name": "ABSOLUTELY PERFECT",
  "series_id": "Nb",
  "series_name": "最新やみつきエステ"
}
```

**完整流程：番号 → 标签/演员**

```
1. GET /v2/search?q=ABP-001&type=movie&limit=1&...   →  data.movies[0].id = "E2630"
2. GET /v4/movies/E2630                              →  data.movie.tags / data.movie.actors
```

---

### 4.3 评论 — `GET /v1/movies/{id}/reviews`

**参数**：`page`（默认 1）、`sort_by`（`hotly` 最热 / 可试 `recent`）、`limit`

**`data.reviews[]`**

```json
{
  "id": 2278183,
  "user_id": 260731,
  "username": "ostonune",
  "watched_count": 2001,
  "status": "watched",
  "status_title": "看過",
  "score": 4,
  "content": "泷泽萝拉的骚是发自骨子里的，制服网袜好评。",
  "likes_count": 38,
  "liked": false,
  "created_at": "2019-01-01T11:02:33.000Z"
}
```

`score` 为 1–5 的整数星级。

---

### 4.4 磁力 — `GET /v1/movies/{id}/magnets`

无分页参数（或忽略），返回 `data.magnets[]`：

```json
{
  "name": "ABP-001-UC.torrent.无码破解",
  "hash": "152287f451afd07d19d136e0980e2628a8fbd3fd",
  "size": 6010,
  "cnsub": true,
  "hd": true,
  "files_count": 1,
  "created_at": "2023-10-29",
  "pikpak_url": "https://keepshare.org/aa36p03v/magnet%3A%3Fxt%3Durn%3Abtih%3A..."
}
```

- `size` 单位 **MB**
- 磁力链接自拼：`magnet:?xt=urn:btih:{hash}`
- `pikpak_url` 是 PikPak 离线转存直链

---

### 4.5 相关清单 — `GET /v1/lists/related`

**参数**：`movie_id`（必填）、`page`、`limit`

**`data.lists[]`** + `data.current_page`

```json
{
  "id": "9n3Qw",
  "name": "极品无码破解(驷马棍哥）",
  "description": null,
  "movies_count": 501,
  "views_count": 543308,
  "collections_count": 25029,
  "is_default": false,
  "share_info": "极品无码破解(驷马棍哥）\nhttps://javdb580.com/lists/9n3Qw",
  "created_at": "2021-05-08T02:56:20.000Z"
}
```

---

### 4.6 播放排行 — `GET /v1/rankings/playback`

**参数**：`period`（`daily` / `weekly` / `monthly`）、`filter_by`（`high_score` 等）

**`data.movies[]`** — 字段同搜索结果的 `movies[]` 摘要。

---

### 4.7 Top250 — `GET /v1/movies/top` ⚠️ 需登录

**参数**：`start_rank=1`、`type=all`、`type_value=`、`ignore_watched=false`、`page`、`limit`

**必须**额外带：

```
authorization: Bearer <token>
```

未登录返回：`{"success":0,"action":"JWTVerificationError","message":"請登錄帳號","data":null}`

---

### 4.8 登录 — `POST /v1/sessions`

**Query 参数**（注意全在 query，不在 body）：

| 参数 | 示例 |
|---|---|
| `username` | 你的账号 |
| `password` | 你的密码 |
| `device_uuid` | 任意 UUID，固定即可 |
| `device_name` | `iPhone` |
| `device_model` | `iPhone` |
| `platform` | `ios` |
| `system_version` | `17.4` |
| `app_version` | `official` |
| `app_version_number` | `1.9.29` |
| `app_channel` | `official` |

**请求头**：

```
user-agent: Dart/3.5 (dart:io)
accept-language: zh-TW
jdsignature: <签名>
content-type: multipart/form-data; boundary=--dio-boundary-2210433284
```

Body 任意（dio 的空 multipart）。失败时返回 `NonExistentUser` 等 action。

成功后从响应中取 token（形如 `data.token` / `data.authorization`，以实际为准），后续接口加 `authorization: Bearer <token>`。

> 不登录也能用：搜索、详情、评论、磁力、演员、标签、排行。仅 Top250 / 个人清单等需登录。

---

### 4.9 演员详情 — `GET /v1/actors/{id}`

`{id}` 来自影片详情的 `actors[].id`（如 `OpzD`）或 `type=actor` 搜索。

**`data` 顶层键**：`share_info`、`has_collected`、`actor`、`filter_tags`、`tags`

**`data.actor`**

```json
{
  "id": "OpzD",
  "type": 0,
  "avatar_url": "https://tp.spfcas.com/rhe951l4q/avatars/op/OpzD.jpg",
  "name": "水咲蘿拉",
  "name_zht": "水咲蘿拉",
  "other_name": "水咲ローラ, 滝澤ローラ, 泷泽萝拉",
  "birthday": null, "age": null, "cons": null, "blood_type": null,
  "height": null, "bust": null, "cup": null, "waist": null, "hips": null,
  "birthplace": null, "twitter_id": "", "instagram_id": "",
  "videos_count": 37
}
```

**`data.tags`** — 该演员作品的标签统计：

```json
[ { "id": "85", "name": "數位馬賽克", "videos_count": 15 }, ... ]
```

**`data.filter_tags`** — 作品列表筛选器（非内容标签）：

```json
[ { "id": "p", "name": "可播放" }, { "id": "s", "name": "單體作品" },
  { "id": "m", "name": "含磁鏈" }, { "id": "c", "name": "含字幕" } ]
```

> **演员作品列表**：未找到独立端点（`/v1/actors/{id}/movies` 等均 404）。变通方案：
> 1. `GET /v4/movies/{id}` 里的 `actor_movies` 字段（部分作品）
> 2. `GET /v2/search?q=<演员名>&type=movie`
> 3. 抓网页 `javdb.com/actors/{id}`（仅限脚本运行在 javdb.com 同源页面时）

---

### 4.10 标签字典 — `GET /v1/tags?type=0`

**参数**：`type` 必填，`0` 或 `1` 均可（内容一致）。缺参返回 `ParameterInvalid`。

**`data.tags[]`** — 按分类分组：

```json
[
  {
    "category": "基本", "category_id": "main",
    "tags": [ {"id": "p", "name": "可播放"}, {"id": "m", "name": "可下載"}, ... ]
  },
  {
    "category": "年份", "category_id": "year",
    "tags": [ {"id": "2026", "name": "2026"}, ... ]
  },
  {
    "category": "主題", "category_id": "subject",
    "tags": [ {"id": "23", "name": "淫亂真實"}, ... ]
  }
]
```

**分类一览**（`category_id`）：

| id | 名称 | 数量约 |
|---|---|---|
| `main` | 基本 | 6 |
| `year` | 年份 | 26 |
| `subject` | 主題 | 60 |
| `role` | 角色 | 53 |
| `cloth` | 服裝 | 39 |
| `body` | 體型 | 20 |
| `behavior` | 行爲 | 40 |
| `play_method` | 玩法 | 37 |
| `category` | 類別 | 58 |
| `duration` | 時長 | 4（`lt-45` / `45-90` / `90-120` / `gt-120`） |

**`main` 的特殊 id**（筛选用，非内容标签）：`p` 可播放、`m` 可下載、`c` 含字幕、`s` 單體影片、`i` 含預覽圖、`v` 含預覽視頻。

---

## 5. 完整可用示例

### Python（零依赖，标准库）

```python
import hashlib, json, re, time, urllib.request

BASE = "https://jdforrepam.com/api"
SECRET = "71cf27bb3c0bcdf207b64abecddc970098c7421ee7203b9cdae54478478a199e7d5a6e1a57691123c1a931c057842fb73ba3b3c83bcd69c17ccf174081e3d8aa"

def sign() -> str:
    ts = str(int(time.time()))
    return f"{ts}.lpw6vgqzsp.{hashlib.md5((ts + SECRET).encode()).hexdigest()}"

def api_get(path: str, **params):
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items() if v is not None)
    url = f"{BASE}{path}" + (f"?{qs}" if qs else "")
    req = urllib.request.Request(url, headers={
        "user-agent": "Dart/3.5 (dart:io)",
        "accept-language": "zh-TW",
        "jdsignature": sign(),
        "jdSignature": sign(),
    })
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode())

def fix_img(url: str) -> str:
    return re.sub(r"https://.*?/rhe951l4q", "https://c0.jdbstatic.com", url or "")

# 番号 → 影片 id
r = api_get("/v2/search", q="ABP-001", type="movie", page=1, limit=1,
            movie_type="all", from_recent="false", movie_filter_by="all",
            movie_sort_by="relevance")
movie_id = r["data"]["movies"][0]["id"]

# 影片 id → 标签 + 演员
d = api_get(f"/v4/movies/{movie_id}")["data"]["movie"]
print("番号:", d["number"])
print("标签:", [t["name"] for t in d["tags"]])
print("演员:", [(a["name"], "女" if a["gender"] == 0 else "男") for a in d["actors"]])
print("导演:", d["director_name"], "| 片商:", d["maker_name"], "| 系列:", d["series_name"])
print("封面:", fix_img(d["cover_url"]))
```

### 浏览器 / Tampermonkey

必须用 `GM_xmlhttpRequest`（跨域），metadata 里加 `@connect jdforrepam.com`。
签名与请求封装直接抄 `emby-javdb-comments.user.js` 或 `JAV-JHS.js` 的 `javDbApi` 对象（646–776 行）。

---

## 6. 番号搜索建议

1. 先用原始番号搜（`ABP-001`）
2. 失败则归一化再搜：去 `-`/`_`/空格、转大写（`ABP001`）
3. 再失败取标题第一个空白分隔 token
4. 匹配时忽略大小写与连字符：`ABP-001` ≈ `ABP001` ≈ `abp001`

---

## 7. 已知限制 & 风险

| 项 | 说明 |
|---|---|
| **密钥会失效** | SECRET 逆向自 App，JavDB 更新 App 后需重新抓包。表现为签名被拒。 |
| **无官方文档** | 私有 API，字段可能随时增删。本文档是 2026-06 实测快照。 |
| **限流** | 未公开阈值。建议串行请求 + 间隔 300–600ms，不要并发轰炸。 |
| **404 vs JSON 错** | 路径不存在返回 HTML 404；参数错误返回 200 JSON。解析时两种都要处理。 |
| **繁体/简体混杂** | `message`、标签名、演员名繁简不一，做匹配时建议归一化。 |
| **`preview_video_url` 带临时 sign** | 有时效，不能长期缓存。 |
| **需登录的接口** | 目前确认仅 `/v1/movies/top` 和 `/v1/lists`（个人清单）。 |
| **无演员作品列表端点** | 见 §4.9 变通方案。 |

---

## 8. 本仓库参考实现

| 文件 | 内容 |
|---|---|
| `emby-javdb-comments.user.js` | `buildSignature()`、纯 JS `md5()`、`searchJavdbMovie()`、`fetchJavdbReviews()` |
| `JAV-JHS.js` | `javDbApi` 对象：搜索/详情/评论/磁力/相关/排行/登录/Top250 全套（646–776 行） |
