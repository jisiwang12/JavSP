"""探测 JavDB App 私有 API 的真实响应结构（只读 GET 请求）"""
import hashlib
import json
import time
import urllib.parse
import urllib.request

BASE = "https://jdforrepam.com/api"
SECRET = ("71cf27bb3c0bcdf207b64abecddc970098c7421ee7203b9cdae54478478a199"
          "e7d5a6e1a57691123c1a931c057842fb73ba3b3c83bcd69c17ccf174081e3d8aa")


def sign():
    ts = str(int(time.time()))
    return f"{ts}.lpw6vgqzsp.{hashlib.md5((ts + SECRET).encode()).hexdigest()}"


def api_get(path, **params):
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items() if v is not None)
    url = f"{BASE}{path}" + (f"?{qs}" if qs else "")
    s = sign()
    req = urllib.request.Request(url, headers={
        "user-agent": "Dart/3.5 (dart:io)",
        "accept-language": "zh-TW",
        "jdsignature": s,
        "jdSignature": s,
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def brief(v, depth=0):
    """递归打印结构，list 只取第一个元素"""
    if isinstance(v, dict):
        return {k: brief(x, depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        return [brief(v[0], depth + 1), f"...({len(v)} items)"] if v else []
    if isinstance(v, str) and len(v) > 80:
        return v[:80] + "..."
    return v


if __name__ == "__main__":
    import sys
    avid = sys.argv[1] if len(sys.argv) > 1 else "IPX-177"

    print("=" * 20, "SEARCH", "=" * 20)
    r = api_get("/v2/search", q=avid, type="movie", page=1, limit=5,
                movie_type="all", from_recent="false",
                movie_filter_by="all", movie_sort_by="relevance")
    print("top-level:", list(r.keys()), "success:", r.get("success"))
    movies = (r.get("data") or {}).get("movies") or []
    print(f"movies count: {len(movies)}")
    if movies:
        print(json.dumps(brief(movies[0]), ensure_ascii=False, indent=2))
        mid = movies[0]["id"]
    else:
        sys.exit("no movie found")

    print("=" * 20, "DETAIL", "=" * 20)
    r2 = api_get(f"/v4/movies/{mid}")
    m = r2["data"]["movie"]
    print(json.dumps(brief(m), ensure_ascii=False, indent=2))
    print("raw type:", m.get("type"), "| has_cnsub:", m.get("has_cnsub"))
    print("tag names:", [t["name"] for t in m.get("tags") or []])
    print("actors:", [(a["name"], a.get("gender")) for a in m.get("actors") or []])

    print("=" * 20, "MAGNETS", "=" * 20)
    r3 = api_get(f"/v1/movies/{mid}/magnets")
    mags = (r3.get("data") or {}).get("magnets") or []
    print("count:", len(mags))
    if mags:
        print(json.dumps(mags[0], ensure_ascii=False, indent=2))

    print("=" * 20, "TAGS DICT", "=" * 20)
    r4 = api_get("/v1/tags", type=0)
    groups = (r4.get("data") or {}).get("tags") or []
    for g in groups:
        sample = [t["name"] for t in (g.get("tags") or [])[:6]]
        print(f"  {g.get('category_id'):12s} {g.get('category'):6s} n={len(g.get('tags') or [])}: {sample}")
