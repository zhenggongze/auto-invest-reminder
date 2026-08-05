#!/usr/bin/env python3
"""查看最后一次运行日志里的PE/PB百分位实际值"""
import urllib.request, json, os, zipfile, io

t = os.environ.get("GITHUB_TOKEN", "")
r = urllib.request.Request(
    "https://api.github.com/repos/zhenggongze/auto-invest-reminder/actions/runs?per_page=5&event=workflow_dispatch",
    headers={"Accept": "application/vnd.github+json", "Authorization": "Bearer " + t, "User-Agent": "python"}
)
data = json.load(urllib.request.urlopen(r, timeout=20))
for w in data["workflow_runs"]:
    rid = w.get("id", 0)
    status = w.get("status", "?")
    conclusion = w.get("conclusion", "?")
    created = w.get("created_at", "?")
    print(f"run {rid}: {status}/{conclusion} {created}")
    if status == "completed":
        logs_url = f"https://api.github.com/repos/zhenggongze/auto-invest-reminder/actions/runs/{rid}/logs"
        r2 = urllib.request.Request(logs_url, headers={"Accept": "application/vnd.github+json", "Authorization": "Bearer " + t, "User-Agent": "python"})
        try:
            zd = urllib.request.urlopen(r2, timeout=20).read()
            z = zipfile.ZipFile(io.BytesIO(zd))
            for fn in z.namelist():
                if "定投偏离度" in fn:
                    content = z.read(fn).decode("utf-8", errors="replace")
                    for line in content.split("\n"):
                        if any(k in line for k in ["PE", "PB", "百分位", "PE/PB"]):
                            if "DEBUG" not in line:
                                print("   ", line.strip()[:200])
        except Exception as e:
            print(f"    log err: {e}")
