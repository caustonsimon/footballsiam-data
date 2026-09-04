import json, time, urllib.request, urllib.error, os

D = os.path.dirname(os.path.abspath(__file__))
REPO = "/Users/User/footballsiam-data"
env = {}
for l in open(f"{REPO}/.env"):
    l = l.strip()
    if l and not l.startswith("#") and "=" in l:
        k, _, v = l.partition("="); env[k.strip()] = v.strip()
import base64
auth = base64.b64encode(f"{env['WP_APP_USERNAME']}:{env['WP_APP_PASSWORD']}".encode()).decode()
H = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}

payloads = json.load(open(f"{D}/reenrich2_payloads.json"))
donef = f"{D}/reenrich2_done.json"
done = set(json.load(open(donef))) if os.path.exists(donef) else set()
logf = open(f"{D}/reenrich2_write.log", "a")

def post(pid, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"https://footballsiam.com/wp-json/wp/v2/match/{pid}",
                                 data=data, method="POST", headers=H)
    for attempt in range(1, 5):
        try:
            return json.load(urllib.request.urlopen(req, timeout=60))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(3 * attempt); continue
            return {"_err": f"HTTP {e.code}: {e.read()[:200]}"}
        except Exception as e:
            if attempt < 4:
                time.sleep(3 * attempt); continue
            return {"_err": str(e)}

ok = fail = 0
failures = []
for i, x in enumerate(payloads):
    pid = x["post_id"]
    if pid in done:
        ok += 1; continue
    resp = post(pid, {"meta": {"raw_match_data": x["raw_match_data"]}})
    if "_err" in resp:
        fail += 1; failures.append((pid, x["fs_id"], resp["_err"]))
        logf.write(f"FAIL {pid} fs={x['fs_id']} {resp['_err']}\n"); logf.flush()
        continue
    got = (resp.get("meta") or {}).get("raw_match_data") or ""
    if got == x["raw_match_data"]:
        ok += 1; done.add(pid)
        logf.write(f"OK {pid} fs={x['fs_id']} len={len(got)}\n")
    else:
        fail += 1; failures.append((pid, x["fs_id"], "verify-mismatch"))
        logf.write(f"MISMATCH {pid} fs={x['fs_id']} got={len(got)} want={len(x['raw_match_data'])}\n"); logf.flush()
    if (i + 1) % 40 == 0:
        json.dump(sorted(done), open(donef, "w"))
        print(f"  ...{i+1}/{len(payloads)} ok={ok} fail={fail}", flush=True)
    time.sleep(0.12)

json.dump(sorted(done), open(donef, "w"))
logf.close()
print(f"DONE ok={ok} fail={fail} total={len(payloads)}")
for f in failures:
    print("  FAIL", f)
