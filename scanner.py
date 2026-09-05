#!/usr/bin/env python3
"""
Multi-chain Meme Coin Scanner
Scans ALL new tokens across ALL chains (Solana, BSC, Base, Polygon, Arbitrum, etc)
Filters by liquidity, volume, and price action to detect x2-x3 pump early.

Run:
  TG_TOKEN=xxx TG_CHAT=yyy python3 scanner.py
  
Or with GitHub Actions (every 5 min):
  Set secrets TG_TOKEN and TG_CHAT in repo settings
"""

import json
import os
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any

# Config
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT", "")
INTERVAL_MIN = int(os.environ.get("INTERVAL_MIN", 5))
RUN_ONCE = os.environ.get("RUN_ONCE", "").lower() in ("1", "true")

# Filters
MIN_LIQ_USD = float(os.environ.get("MIN_LIQ_USD", 50_000))      # Liquidity > 50k
MIN_VOL_1H_USD = float(os.environ.get("MIN_VOL_1H_USD", 100_000))  # Volume > 100k
MIN_PRICE_CHANGE_1H = float(os.environ.get("MIN_PRICE_CHANGE_1H", 20))  # +20% in 1h
MIN_BUYS_1H = int(os.environ.get("MIN_BUYS_1H", 50))           # At least 50 buys
MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", 48))       # Token < 48h old

# State file to avoid duplicate alerts
STATE_FILE = Path("scanner_seen.json")
API_BASE = "https://api.geckoterminal.com/api/v2"

# All major chains
CHAINS = [
    "solana",
    "bsc",           # Binance Smart Chain
    "ethereum",
    "base",
    "arbitrum",
    "polygon",
    "optimism",
    "avalanche",
    "fantom",
]

def log(msg: str):
    """Print with timestamp"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")

def get_json(url: str) -> Optional[Dict]:
    """Fetch JSON from URL, return None on error"""
    try:
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "meme-scanner/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except Exception as e:
        log(f"❌ API error {url}: {e}")
        return None

def send_tg(msg: str):
    """Send message to Telegram (non-blocking, silent fail)"""
    if not TG_TOKEN or not TG_CHAT:
        log(f"⚠️  Telegram not configured, message not sent:\n{msg}")
        return
    try:
        data = urllib.parse.urlencode({
            "chat_id": TG_CHAT,
            "text": msg,
            "disable_web_page_preview": "1"
        }).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data,
            timeout=10
        )
        log(f"✅ Alert sent to Telegram")
    except Exception as e:
        log(f"⚠️  Telegram send error: {e}")

def safe_float(val: Any, default: float = 0.0) -> float:
    """Safely convert to float"""
    try:
        if val is None:
            return default
        return float(val)
    except (TypeError, ValueError):
        return default

def get_pools(chain: str) -> List[Dict]:
    """Fetch trending + new pools from GeckoTerminal for a chain"""
    pools = []
    endpoints = [
        f"{API_BASE}/networks/{chain}/trending_pools?duration=1h",
        f"{API_BASE}/networks/{chain}/new_pools?page=1",
    ]
    for url in endpoints:
        data = get_json(url)
        if data and "data" in data:
            pools.extend(data["data"])
            time.sleep(0.5)  # Rate limit
    return pools

def scan_chain(chain: str, seen: Dict) -> List[str]:
    """Scan a single chain, return list of alert messages"""
    log(f"🔍 Scanning {chain}...")
    alerts = []
    pools = get_pools(chain)
    
    if not pools:
        log(f"⚠️  No pools found for {chain}")
        return alerts
    
    now = datetime.now(timezone.utc)
    
    for pool in pools:
        pool_id = pool.get("id")
        if not pool_id or pool_id in seen:
            continue
        
        attrs = pool.get("attributes", {})
        
        # Age
        created_at = attrs.get("pool_created_at")
        if created_at:
            try:
                pool_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                age_h = (now - pool_time).total_seconds() / 3600
            except:
                age_h = 999
        else:
            age_h = 999
        
        if age_h > MAX_AGE_HOURS:
            continue
        
        # Liquidity
        liq = safe_float(attrs.get("reserve_in_usd"))
        if liq < MIN_LIQ_USD:
            continue
        
        # Volume 1h
        vol = safe_float((attrs.get("volume_usd") or {}).get("h1"))
        if vol < MIN_VOL_1H_USD:
            continue
        
        # Price change 1h
        chg = safe_float((attrs.get("price_change_percentage") or {}).get("h1"))
        if chg < MIN_PRICE_CHANGE_1H:
            continue
        
        # Transactions 1h
        txs_1h = attrs.get("transactions", {}).get("h1", {})
        buys = int(txs_1h.get("buys", 0))
        sells = int(txs_1h.get("sells", 0))
        if buys < MIN_BUYS_1H:
            continue
        
        # All filters passed - mark as seen and create alert
        seen[pool_id] = time.time()
        
        name = attrs.get("name", "?").strip()
        symbol = attrs.get("symbol", "?").strip()
        price = safe_float(attrs.get("price_usd"))
        market_cap = safe_float(attrs.get("market_cap_usd") or attrs.get("fdv_usd"))
        
        # Pool URL
        addr = pool_id.split("_", 1)[1] if "_" in pool_id else pool_id
        pool_url = f"https://www.geckoterminal.com/{chain}/pools/{addr}"
        
        # Format alert
        buy_sell_ratio = buys / max(sells, 1)
        alert = (
            f"🚀 NEW TOKEN ALERT\n"
            f"─────────────────────────\n"
            f"💰 {name} ({symbol})\n"
            f"⛓️  Chain: {chain.upper()}\n"
            f"\n"
            f"📊 Stats:\n"
            f"  • MCap: ${market_cap:,.0f}\n"
            f"  • Liq: ${liq:,.0f}\n"
            f"  • Vol 1h: ${vol:,.0f}\n"
            f"  • Age: {age_h:.1f}h\n"
            f"\n"
            f"📈 1h Action:\n"
            f"  • Price: {chg:+.1f}%\n"
            f"  • Buys: {buys} | Sells: {sells}\n"
            f"  • B/S Ratio: {buy_sell_ratio:.2f}x\n"
            f"\n"
            f"🔗 {pool_url}\n"
            f"\n"
            f"⚠️  DO YOUR OWN RESEARCH\n"
            f"Check: holders, top 10 wallet %, dev wallet, narrativa"
        )
        alerts.append(alert)
        log(f"🎯 FOUND: {symbol} on {chain} (+{chg:.0f}%) - {buy_sell_ratio:.2f}x B/S")
    
    return alerts

def main():
    log("\n" + "="*50)
    log("🔥 MEME COIN SCANNER v1.0")
    log(f"Chains: {', '.join(CHAINS)}")
    log(f"Filters: Liq>${MIN_LIQ_USD/1e3:.0f}k | Vol>${MIN_VOL_1H_USD/1e3:.0f}k | +{MIN_PRICE_CHANGE_1H}% | Buys>{MIN_BUYS_1H}")
    log("="*50 + "\n")
    
    # Load state
    seen = {}
    if STATE_FILE.exists():
        try:
            seen = json.loads(STATE_FILE.read_text())
        except:
            pass
    
    # Cleanup old entries (>7 days)
    cutoff = time.time() - (7 * 86400)
    seen = {k: v for k, v in seen.items() if v > cutoff}
    
    # Initial message
    if TG_TOKEN and TG_CHAT:
        send_tg(f"🤖 Scanner online\nChains: {', '.join(CHAINS)}\nInterval: {INTERVAL_MIN}m")
    
    # Main loop
    iteration = 0
    while True:
        iteration += 1
        log(f"\n📡 Scan cycle #{iteration}")
        
        all_alerts = []
        for chain in CHAINS:
            try:
                alerts = scan_chain(chain, seen)
                all_alerts.extend(alerts)
            except Exception as e:
                log(f"❌ Error scanning {chain}: {e}")
        
        # Send alerts
        for alert in all_alerts:
            send_tg(alert)
            time.sleep(1)  # Avoid Telegram rate limit
        
        if all_alerts:
            log(f"✅ Sent {len(all_alerts)} alert(s)")
        else:
            log(f"ℹ️  No new tokens found")
        
        # Save state
        STATE_FILE.write_text(json.dumps(seen))
        
        # Exit if RUN_ONCE
        if RUN_ONCE:
            log(f"✅ Run-once mode - exiting")
            break
        
        # Wait
        log(f"⏰ Waiting {INTERVAL_MIN}m until next scan...")
        time.sleep(INTERVAL_MIN * 60)

if __name__ == "__main__":
    main()
