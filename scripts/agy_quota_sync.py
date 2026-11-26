#!/usr/bin/env python3
"""
Antigravity Real Quota -> 24/7 LIVE RPG Office Full-Auto Bridge (`agy_quota_sync.py`)
====================================================================================
Zero-dependency Python 3 script (macOS / Windows / Linux) that automatically captures
real Google Antigravity model quotas and syncs them into:
  `howardKung/project-lifeblood-rpg-office/state.json`

Supported Modes:
1. Official `agy` CLI Statusline Hook (`--statusline`):
   Reads documented statusLine JSON payload from stdin, prints a fast 1-line terminal
   quota bar (<30ms), and spawns a non-blocking background sync whenever quota changes.
2. Desktop IDE / `agusage` Bridge (`--once` or `--daemon`):
   Reads local Antigravity IDE quota via `agusage --json` (if installed) or cached
   `agy` payload and syncs changes to GitHub automatically.
3. Direct Calibration (`--set <pct>`):
   Immediately syncs a specified quota percentage & optional bucket details to GitHub.
4. One-Click Local Setup (`--configure-agy`):
   Safely wires `statusLine` in `~/.gemini/antigravity-cli/settings.json` with
   `"stack_with_default": true` so built-in credits + custom RPG sync run together.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

REPO_SLUG = os.environ.get("RPG_OFFICE_REPO", "howardKung/project-lifeblood-rpg-office")
STATE_API_PATH = f"repos/{REPO_SLUG}/contents/state.json"
STATE_HTTPS_URL = f"https://api.github.com/{STATE_API_PATH}"
TZ_TW = timezone(timedelta(hours=8))

MIN_SYNC_INTERVAL_SEC = 300  # Heartbeat sync at most every 5 min if % unchanged
MIN_PCT_DELTA = 1            # Immediate background sync on >= 1% quota change


def get_config_dir() -> Path:
    home = Path.home()
    d = home / ".gemini" / "antigravity-cli"
    d.mkdir(parents=True, exist_ok=True)
    return d


CACHE_STATE_PATH = get_config_dir() / "rpg_office_sync_cache.json"
LATEST_PAYLOAD_PATH = get_config_dir() / "rpg_office_latest_quota.json"
ENV_CONFIG_PATH = get_config_dir() / "rpg_office_env.json"


def now_tw_str() -> str:
    return datetime.now(TZ_TW).strftime("%Y-%m-%d %H:%M:%S")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_duration_short(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return ""
    s = int(seconds)
    if s >= 86400:
        d = s // 86400
        h = (s % 86400) // 3600
        return f"{d}d{h}h"
    h = s // 3600
    m = (s % 3600) // 60
    if h > 0:
        return f"{h}h{m:02d}m"
    return f"{m}m"


def parse_bucket(raw: Any, label: str) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    if raw.get("disabled") is True:
        return None
    frac = raw.get("remaining_fraction")
    if frac is None:
        return None
    try:
        pct = max(0, min(100, round(float(frac) * 100)))
    except (TypeError, ValueError):
        return None

    reset_time = raw.get("reset_time")
    reset_sec = raw.get("reset_in_seconds")
    if reset_time and reset_sec is None:
        try:
            rt = datetime.fromisoformat(str(reset_time).replace("Z", "+00:00"))
            reset_sec = max(0, int((rt - datetime.now(timezone.utc)).total_seconds()))
        except ValueError:
            reset_sec = None
    elif reset_sec is not None and not reset_time:
        try:
            reset_sec = max(0, int(float(reset_sec)))
            rt = datetime.now(timezone.utc) + timedelta(seconds=reset_sec)
            reset_time = rt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError):
            reset_sec = None

    return {
        "label": label,
        "pct": pct,
        "remainingFraction": round(float(frac), 4),
        "resetTime": reset_time,
        "resetInSeconds": int(reset_sec) if isinstance(reset_sec, (int, float)) else None,
    }


def normalize_agy_payload(payload: dict[str, Any], source: str = "agy-statusline") -> Optional[dict[str, Any]]:
    quota_raw = payload.get("quota")
    if not isinstance(quota_raw, dict) or not quota_raw:
        return None

    b_g5h = parse_bucket(quota_raw.get("gemini-5h"), "Gemini 5h")
    b_gwk = parse_bucket(quota_raw.get("gemini-weekly"), "Gemini 週額度")
    b_arg = parse_bucket(quota_raw.get("gemini-argon-wtu-weekly"), "Argon 週額度")
    b_3p5 = parse_bucket(quota_raw.get("3p-5h"), "Claude/GPT 5h")
    b_3pw = parse_bucket(quota_raw.get("3p-weekly"), "Claude/GPT 週額度")

    buckets: dict[str, dict[str, Any]] = {}
    if b_g5h:
        buckets["gemini5h"] = b_g5h
    if b_gwk:
        buckets["geminiWeekly"] = b_gwk
    if b_arg:
        buckets["argonWeekly"] = b_arg
    if b_3p5:
        buckets["thirdParty5h"] = b_3p5
    if b_3pw:
        buckets["thirdPartyWeekly"] = b_3pw

    # Also handle any future/unknown quota bucket names gracefully
    if not buckets:
        for k, v in quota_raw.items():
            parsed = parse_bucket(v, k)
            if parsed:
                buckets[k] = parsed

    if not buckets:
        return None

    # Primary effective quota for Gemini HQ agents = bottleneck of Gemini 5h & Gemini Weekly
    gemini_candidates = [b["pct"] for k, b in buckets.items() if k in ("gemini5h", "geminiWeekly")]
    if gemini_candidates:
        effective_pct = min(gemini_candidates)
    else:
        effective_pct = min(b["pct"] for b in buckets.values())

    primary_reset = (
        (b_g5h or {}).get("resetTime")
        or (b_gwk or {}).get("resetTime")
        or next((b.get("resetTime") for b in buckets.values() if b.get("resetTime")), None)
    )
    primary_reset_sec = (
        (b_g5h or {}).get("resetInSeconds")
        or (b_gwk or {}).get("resetInSeconds")
        or next((b.get("resetInSeconds") for b in buckets.values() if b.get("resetInSeconds") is not None), None)
    )

    model_obj = payload.get("model") or {}
    active_model = (
        model_obj.get("display_name")
        or model_obj.get("id")
        if isinstance(model_obj, dict)
        else str(model_obj or "")
    )

    return {
        "effectivePct": effective_pct,
        "source": source,
        "planTier": payload.get("plan_tier") or "",
        "activeModel": active_model or "",
        "agentState": payload.get("agent_state") or "",
        "primaryResetTime": primary_reset,
        "primaryResetInSeconds": primary_reset_sec,
        "syncedAt": now_tw_str(),
        "syncedAtUtc": now_utc_iso(),
        "buckets": buckets,
    }


def format_statusline_bar(norm: Optional[dict[str, Any]], raw_payload: dict[str, Any]) -> str:
    if not norm:
        state_lbl = (raw_payload.get("agent_state") or "ready").upper()
        return f"🏛️ RPG總部連線待命 │ 狀態: {state_lbl} (輸入 /usage 載入即時額度)"

    b = norm["buckets"]
    parts = []
    if "gemini5h" in b:
        g5 = b["gemini5h"]
        rst = fmt_duration_short(g5.get("resetInSeconds"))
        rst_str = f" ⏱️{rst}" if rst else ""
        icon = "🟢" if g5["pct"] > 35 else ("🟡" if g5["pct"] > 0 else "🔴")
        parts.append(f"{icon} Gemini 5h: {g5['pct']}%{rst_str}")
    if "geminiWeekly" in b:
        gw = b["geminiWeekly"]
        parts.append(f"週: {gw['pct']}%")
    if "thirdParty5h" in b:
        tp = b["thirdParty5h"]
        parts.append(f"3P: {tp['pct']}%")
    elif "thirdPartyWeekly" in b:
        tp = b["thirdPartyWeekly"]
        parts.append(f"3P週: {tp['pct']}%")

    ctx = (raw_payload.get("context_window") or {}).get("used_percentage")
    if isinstance(ctx, (int, float)):
        parts.append(f"Ctx: {ctx:.0f}%")

    parts.append("🏛️ RPG總部自動同步")
    return " │ ".join(parts)


def load_sync_cache() -> dict[str, Any]:
    if CACHE_STATE_PATH.exists():
        try:
            return json.loads(CACHE_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_sync_cache(data: dict[str, Any]) -> None:
    try:
        CACHE_STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def should_trigger_sync(norm: dict[str, Any]) -> bool:
    cache = load_sync_cache()
    last_pct = cache.get("lastPushedPct")
    last_ts = float(cache.get("lastPushedEpoch", 0))
    cur_pct = norm["effectivePct"]
    elapsed = time.time() - last_ts

    if last_pct is None:
        return True
    if (cur_pct == 0 and last_pct != 0) or (last_pct == 0 and cur_pct > 0):
        return True
    if abs(cur_pct - int(last_pct)) >= MIN_PCT_DELTA and elapsed >= 15:
        return True
    if elapsed >= MIN_SYNC_INTERVAL_SEC:
        return True
    return False


def spawn_detached_push() -> None:
    cmd = [sys.executable, str(Path(__file__).resolve()), "--push-cached"]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(
            subprocess, "DETACHED_PROCESS", 0x00000008
        )
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kwargs)
    except Exception:
        pass


def resolve_github_token() -> Optional[str]:
    for env_key in ("RPG_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        val = os.environ.get(env_key, "").strip()
        if val:
            return val
    if ENV_CONFIG_PATH.exists():
        try:
            cfg = json.loads(ENV_CONFIG_PATH.read_text(encoding="utf-8"))
            tok = str(cfg.get("githubToken") or "").strip()
            if tok:
                return tok
        except Exception:
            pass
    return None


def gh_cli_available() -> bool:
    return shutil.which("gh") is not None


def github_get_state_file(token: Optional[str]) -> tuple[dict[str, Any], str]:
    """Returns (parsed_state_json, blob_sha)."""
    if token:
        req = urllib.request.Request(
            f"{STATE_HTTPS_URL}?ref=main",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "Antigravity-RPG-QuotaBridge/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
        raw_bytes = base64.b64decode(meta["content"])
        return json.loads(raw_bytes.decode("utf-8")), meta["sha"]

    if gh_cli_available():
        res = subprocess.run(
            ["gh", "api", f"{STATE_API_PATH}?ref=main"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if res.returncode == 0:
            meta = json.loads(res.stdout)
            raw_bytes = base64.b64decode(meta["content"])
            return json.loads(raw_bytes.decode("utf-8")), meta["sha"]
        raise RuntimeError(f"gh api GET failed: {res.stderr.strip()}")

    raise RuntimeError(
        "No GitHub credentials found. Either run `gh auth login` or set GITHUB_TOKEN / ~/.gemini/antigravity-cli/rpg_office_env.json"
    )


def github_put_state_file(state_obj: dict[str, Any], sha: str, commit_msg: str, token: Optional[str]) -> None:
    encoded = base64.b64encode((json.dumps(state_obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")).decode("ascii")
    body = {
        "message": commit_msg,
        "content": encoded,
        "sha": sha,
        "branch": "main",
    }

    if token:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            STATE_HTTPS_URL,
            data=data,
            method="PUT",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "Antigravity-RPG-QuotaBridge/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        return

    if gh_cli_available():
        res = subprocess.run(
            ["gh", "api", "--method", "PUT", STATE_API_PATH, "--input", "-"],
            input=json.dumps(body),
            capture_output=True,
            text=True,
            timeout=25,
        )
        if res.returncode != 0:
            raise RuntimeError(f"gh api PUT failed: {res.stderr.strip()}")
        return

    raise RuntimeError("No GitHub credentials available for PUT.")


def apply_quota_to_office_state(state: dict[str, Any], norm: dict[str, Any]) -> bool:
    """Mutates `state` in-place with new live quota. Returns True if meaningful change occurred."""
    q = state.setdefault("quota", {})
    prev_pct = int(q.get("remaining", 0))
    new_pct = int(norm["effectivePct"])
    prev_live = q.get("live") or {}

    # Check if nothing meaningful changed
    if (
        prev_live.get("enabled") is True
        and prev_pct == new_pct
        and prev_live.get("primaryResetTime") == norm.get("primaryResetTime")
        and prev_live.get("buckets") == norm.get("buckets")
    ):
        return False

    if new_pct < prev_pct:
        q["totalSpent"] = int(q.get("totalSpent", 0)) + (prev_pct - new_pct)

    q["unit"] = "真實算力額度 % (Live Quota)"
    q["remaining"] = new_pct
    q["max"] = 100
    q["autoClockOutOnZero"] = True
    q["live"] = {
        "enabled": True,
        "source": norm.get("source", "agy-statusline"),
        "planTier": norm.get("planTier", ""),
        "activeModel": norm.get("activeModel", ""),
        "primaryResetTime": norm.get("primaryResetTime"),
        "syncedAt": norm.get("syncedAt", now_tw_str()),
        "syncedAtUtc": norm.get("syncedAtUtc", now_utc_iso()),
        "buckets": norm.get("buckets", {}),
    }

    ts = norm.get("syncedAt", now_tw_str())
    reports = state.setdefault("reports", [])

    # Handle 0% auto clock-out vs recovery wake-up
    zero_quota_bots = {"paper_fund_manager", "news_social_radar"}
    if new_pct <= 0:
        for a in state.get("agents", []):
            if a.get("isBoss") or a.get("id") in zero_quota_bots:
                continue
            a["status"] = "depleted"
            a["quota"] = 0
            rst_hint = ""
            if norm.get("primaryResetInSeconds"):
                rst_hint = f"（預計 {fmt_duration_short(norm['primaryResetInSeconds'])} 後重置）"
            a["currentTask"] = f"真實算力額度已達上限，自動打卡休息中{rst_hint}"
            a["lastUpdate"] = ts

        if prev_pct > 0:
            reports.insert(
                0,
                {
                    "id": f"auto-quota-depleted-{ts[:13].replace(' ', '-')}",
                    "time": ts,
                    "agentId": "office_steward",
                    "agentName": "集團總務長",
                    "status": "depleted",
                    "cost": 0,
                    "summary": f"🪫【真實額度自動監測歸零】Antigravity 即時額度回報 0%，AI 成員已自動打卡移至休息區（0-額度 GitHub Actions 操盤與新聞排程照常運作）。",
                },
            )
    else:
        woke_any = False
        for a in state.get("agents", []):
            if a.get("isBoss"):
                continue
            a["quota"] = new_pct
            if a.get("status") == "depleted":
                a["status"] = "idle"
                a["currentTask"] = f"真實算力額度已恢復至 {new_pct}%，待命準備上工"
                a["lastUpdate"] = ts
                woke_any = True

        if prev_pct <= 0 or woke_any:
            reports.insert(
                0,
                {
                    "id": f"auto-quota-refill-{ts[:13].replace(' ', '-')}",
                    "time": ts,
                    "agentId": "office_steward",
                    "agentName": "集團總務長",
                    "status": "working",
                    "cost": 0,
                    "summary": f"🔋【真實額度自動回血 {new_pct}%】本機 Antigravity 額度橋接器自動回報額度恢復至 {new_pct}%，全員已解除休息狀態待命！",
                },
            )

    state["reports"] = reports[:50]
    return True


def push_normalized_quota(norm: dict[str, Any], verbose: bool = False) -> bool:
    token = resolve_github_token()
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            state, sha = github_get_state_file(token)
            changed = apply_quota_to_office_state(state, norm)
            if not changed:
                save_sync_cache(
                    {
                        "lastPushedPct": norm["effectivePct"],
                        "lastPushedEpoch": time.time(),
                        "lastSyncedAt": norm["syncedAt"],
                    }
                )
                if verbose:
                    print(f"[QUOTA-SYNC] Already up-to-date at {norm['effectivePct']}% (no commit needed).")
                return False

            msg = f"⚡ Auto-sync real Antigravity quota: {norm['effectivePct']}% ({norm['syncedAt']})"
            github_put_state_file(state, sha, msg, token)
            save_sync_cache(
                {
                    "lastPushedPct": norm["effectivePct"],
                    "lastPushedEpoch": time.time(),
                    "lastSyncedAt": norm["syncedAt"],
                }
            )
            if verbose:
                print(f"[QUOTA-SYNC] Pushed real quota {norm['effectivePct']}% -> {REPO_SLUG}/state.json")
            return True
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 409 and attempt < 2:
                time.sleep(0.8 * (attempt + 1))
                continue
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
                continue
            break

    if verbose and last_err:
        print(f"[QUOTA-SYNC ERROR] {last_err}", file=sys.stderr)
    return False


def try_read_agusage_cli() -> Optional[dict[str, Any]]:
    """Reads quota JSON from open-source `agusage` / `agu` CLI if installed locally."""
    exe = shutil.which("agusage") or shutil.which("agu")
    if not exe:
        return None
    try:
        res = subprocess.run([exe, "--json"], capture_output=True, text=True, timeout=8)
        if res.returncode != 0 or not res.stdout.strip():
            return None
        data = json.loads(res.stdout)
    except Exception:
        return None

    # Handle common agusage JSON structures
    buckets: dict[str, dict[str, Any]] = {}
    items = data if isinstance(data, list) else (data.get("models") or data.get("quotas") or data.get("pools") or [])
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("label") or item.get("model") or "")
            rem = item.get("remaining") if item.get("remaining") is not None else item.get("remainingPct")
            if rem is None and item.get("remaining_fraction") is not None:
                rem = float(item["remaining_fraction"]) * 100
            if rem is None:
                continue
            pct = max(0, min(100, round(float(rem))))
            lname = name.lower()
            key = "gemini5h" if "gemini" in lname else ("thirdParty5h" if ("claude" in lname or "gpt" in lname) else name)
            buckets[key] = {
                "label": name or key,
                "pct": pct,
                "resetTime": item.get("resetTime") or item.get("reset_time"),
                "resetInSeconds": item.get("resetInSeconds") or item.get("reset_in_seconds"),
            }

    if not buckets:
        return None
    eff = buckets["gemini5h"]["pct"] if "gemini5h" in buckets else min(b["pct"] for b in buckets.values())
    return {
        "effectivePct": eff,
        "source": "agusage-local-ide",
        "planTier": str(data.get("plan") or "") if isinstance(data, dict) else "",
        "activeModel": "",
        "agentState": "idle",
        "primaryResetTime": next((b.get("resetTime") for b in buckets.values() if b.get("resetTime")), None),
        "primaryResetInSeconds": next((b.get("resetInSeconds") for b in buckets.values() if b.get("resetInSeconds")), None),
        "syncedAt": now_tw_str(),
        "syncedAtUtc": now_utc_iso(),
        "buckets": buckets,
    }


def configure_local_agy_settings(github_token: str = "") -> None:
    script_abs = str(Path(__file__).resolve())
    py_cmd = "python" if os.name == "nt" else "python3"
    hook_cmd = f'{py_cmd} "{script_abs}" --statusline'

    if github_token.strip():
        ENV_CONFIG_PATH.write_text(
            json.dumps({"githubToken": github_token.strip()}, indent=2) + "\n",
            encoding="utf-8",
        )
        if os.name != "nt":
            ENV_CONFIG_PATH.chmod(0o600)
        print(f"[OK] Saved GitHub token to {ENV_CONFIG_PATH}")

    candidate_paths = [Path.home() / ".gemini" / "antigravity-cli" / "settings.json"]
    if sys.platform == "darwin":
        mac_alt = Path.home() / "Library" / "Application Support" / "antigravity-cli" / "settings.json"
        if mac_alt.parent.exists():
            candidate_paths.append(mac_alt)
    elif os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            win_alt = Path(appdata) / "antigravity-cli" / "settings.json"
            if win_alt.parent.exists():
                candidate_paths.append(win_alt)

    for settings_path in candidate_paths:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        if settings_path.exists():
            try:
                existing = json.loads(settings_path.read_text(encoding="utf-8"))
            except Exception:
                backup = settings_path.with_suffix(".json.bak")
                shutil.copy2(settings_path, backup)
                existing = {}

        existing["statusLine"] = {
            "type": "command",
            "command": hook_cmd,
            "stack_with_default": True,
        }
        settings_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[OK] Wired Antigravity CLI statusLine -> {settings_path}")

    print("\n🎉 全自動額度橋接安裝完成！")
    print(f"   Statusline 指令: {hook_cmd}")
    print(f"   目標辦公室倉庫  : https://howardkung.github.io/project-lifeblood-rpg-office/")


def cmd_statusline() -> None:
    raw_in = sys.stdin.read().strip()
    if not raw_in:
        print("🏛️ RPG總部連線待命")
        return
    try:
        payload = json.loads(raw_in)
    except Exception:
        print("🏛️ RPG總部連線待命")
        return

    norm = normalize_agy_payload(payload, source="agy-statusline")
    print(format_statusline_bar(norm, payload))
    sys.stdout.flush()

    if norm:
        try:
            LATEST_PAYLOAD_PATH.write_text(json.dumps(norm, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        if should_trigger_sync(norm):
            spawn_detached_push()


def main() -> None:
    p = argparse.ArgumentParser(description="Antigravity Real Quota -> RPG Office Full-Auto Sync Bridge")
    p.add_argument("--statusline", action="store_true", help="Read agy statusLine JSON from stdin & background-sync")
    p.add_argument("--push-cached", action="store_true", help="Internal worker: push latest cached quota to GitHub")
    p.add_argument("--once", action="store_true", help="Sync once immediately from agusage CLI or latest agy cache")
    p.add_argument("--daemon", action="store_true", help="Run background watcher syncing IDE/CLI quota every N seconds")
    p.add_argument("--interval", type=int, default=300, help="Poll interval in seconds for --daemon (default: 300)")
    p.add_argument("--set", type=int, metavar="PCT", help="Directly sync a specific Gemini 5h quota percentage (0-100)")
    p.add_argument("--weekly-pct", type=int, default=None, help="Optional Gemini weekly quota percentage (0-100)")
    p.add_argument("--third-party-pct", type=int, default=None, help="Optional Claude/GPT quota percentage (0-100)")
    p.add_argument("--reset-in-sec", type=int, default=None, help="Optional seconds until 5h quota reset")
    p.add_argument("--plan", default="", help="Optional plan tier label (e.g. 'Google AI Pro')")
    p.add_argument("--source", default="cli-sync", help="Telemetry source tag")
    p.add_argument("--configure-agy", action="store_true", help="Auto-configure ~/.gemini/antigravity-cli/settings.json")
    p.add_argument("--github-token", default="", help="Optional GitHub PAT to store for push auth")

    args = p.parse_args()

    if args.statusline:
        cmd_statusline()
        return

    if args.configure_agy:
        configure_local_agy_settings(github_token=args.github_token)
        return

    if args.push_cached:
        if LATEST_PAYLOAD_PATH.exists():
            norm = json.loads(LATEST_PAYLOAD_PATH.read_text(encoding="utf-8"))
            push_normalized_quota(norm, verbose=False)
        return

    if args.set is not None:
        g5 = max(0, min(100, args.set))
        reset_time = None
        if args.reset_in_sec is not None and args.reset_in_sec > 0:
            rt = datetime.now(timezone.utc) + timedelta(seconds=args.reset_in_sec)
            reset_time = rt.strftime("%Y-%m-%dT%H:%M:%SZ")

        buckets: dict[str, dict[str, Any]] = {
            "gemini5h": {
                "label": "Gemini 5h",
                "pct": g5,
                "resetTime": reset_time,
                "resetInSeconds": args.reset_in_sec,
            }
        }
        eff = g5
        if args.weekly_pct is not None:
            gw = max(0, min(100, args.weekly_pct))
            buckets["geminiWeekly"] = {"label": "Gemini 週額度", "pct": gw, "resetTime": None, "resetInSeconds": None}
            eff = min(eff, gw)
        if args.third_party_pct is not None:
            tp = max(0, min(100, args.third_party_pct))
            buckets["thirdParty5h"] = {"label": "Claude/GPT 5h", "pct": tp, "resetTime": None, "resetInSeconds": None}

        norm = {
            "effectivePct": eff,
            "source": args.source,
            "planTier": args.plan,
            "activeModel": "",
            "agentState": "working",
            "primaryResetTime": reset_time,
            "primaryResetInSeconds": args.reset_in_sec,
            "syncedAt": now_tw_str(),
            "syncedAtUtc": now_utc_iso(),
            "buckets": buckets,
        }
        push_normalized_quota(norm, verbose=True)
        return

    if args.once:
        norm = try_read_agusage_cli()
        if not norm and LATEST_PAYLOAD_PATH.exists():
            norm = json.loads(LATEST_PAYLOAD_PATH.read_text(encoding="utf-8"))
        if not norm:
            print("[QUOTA-SYNC] No local agusage CLI output or cached agy statusLine data found yet.", file=sys.stderr)
            sys.exit(1)
        push_normalized_quota(norm, verbose=True)
        return

    if args.daemon:
        print(f"[QUOTA-DAEMON] Watching local Antigravity quota every {args.interval}s -> {REPO_SLUG} ...")
        while True:
            norm = try_read_agusage_cli()
            if not norm and LATEST_PAYLOAD_PATH.exists():
                try:
                    norm = json.loads(LATEST_PAYLOAD_PATH.read_text(encoding="utf-8"))
                except Exception:
                    norm = None
            if norm and should_trigger_sync(norm):
                push_normalized_quota(norm, verbose=True)
            time.sleep(max(30, args.interval))

    p.print_help()


if __name__ == "__main__":
    main()
