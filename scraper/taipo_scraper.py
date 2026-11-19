import json
import os
import random
import re
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from io import BytesIO

import requests
from bs4 import BeautifulSoup
from PIL import Image
import imagehash

HKT = timezone(timedelta(hours=8))
DATA_PATH = "data/taipo_listings.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
}

TAIPO_ESTATE_DICT = {
    "大埔中心": {"sub_district": "大埔市中心", "aliases": ["大埔中心", "Tai Po Centre"]},
    "太湖花園": {"sub_district": "太和", "aliases": ["太湖花園", "太湖花園1期", "太湖花園2期", "Serenity Park"]},
    "昌運中心": {"sub_district": "大埔市中心", "aliases": ["昌運中心", "Fortune Plaza"]},
    "大埔廣場": {"sub_district": "大埔市中心", "aliases": ["大埔廣場", "Tai Po Plaza"]},
    "新達廣場": {"sub_district": "大埔市中心", "aliases": ["新達廣場", "Uptown Plaza"]},
    "寶雅苑": {"sub_district": "太和", "aliases": ["寶雅苑", "Po Nga Court"]},
    "翠屏花園": {"sub_district": "大埔市中心", "aliases": ["翠屏花園", "Jade Plaza"]},
    "太和邨": {"sub_district": "太和", "aliases": ["太和邨", "Tai Wo Estate"]},
    "富善邨": {"sub_district": "大埔市中心", "aliases": ["富善邨", "Fu Shin Estate"]},
    "天賦海灣": {"sub_district": "白石角", "aliases": ["天賦海灣", "溋玥", "海鑽", "Providence Bay"]},
    "逸瓏灣": {"sub_district": "白石角", "aliases": ["逸瓏灣", "逸瓏灣8", "Mayfair By The Sea"]},
    "嘉熙": {"sub_district": "白石角", "aliases": ["嘉熙", "Solaria"]},
    "海日灣": {"sub_district": "白石角", "aliases": ["海日灣", "海日灣II", "Centra Horizon"]},
    "朗濤": {"sub_district": "白石角", "aliases": ["朗濤", "Ontolo"]},
    "雲滙": {"sub_district": "白石角", "aliases": ["雲滙", "St Martin"]},
    "Silicon Hill": {"sub_district": "白石角", "aliases": ["Silicon Hill", "University Hill"]},
    "嵐山": {"sub_district": "大埔半山 / 康樂園", "aliases": ["嵐山", "嵐山第1期", "嵐山第2期", "Mont Vert"]},
    "上然": {"sub_district": "大埔半山 / 康樂園", "aliases": ["上然", "上然2期", "Le Mont"]},
    "帝欣苑": {"sub_district": "大埔半山 / 康樂園", "aliases": ["帝欣苑", "Classical Gardens"]},
    "康樂園": {"sub_district": "大埔半山 / 康樂園", "aliases": ["康樂園", "Hong Lok Yuen"]},
    "比華利山別墅": {"sub_district": "大埔半山 / 康樂園", "aliases": ["比華利山別墅", "The Beverly Hills"]},
}


def polite_sleep(base: float = 1.6):
    time.sleep(base + random.uniform(0.3, 1.2))


def normalize_estate(raw_name: str):
    clean = re.sub(r"\s+", "", raw_name or "")
    for std_name, meta in TAIPO_ESTATE_DICT.items():
        for alias in meta["aliases"]:
            if alias.replace(" ", "").lower() in clean.lower():
                return std_name, meta["sub_district"]
    return (raw_name or "大埔屋苑").strip(), "大埔其他"


def normalize_block(raw_text: str) -> str:
    m = re.search(r"([A-Za-z0-9一二三四五六七八九十]+座|洋房)", raw_text or "")
    return m.group(1).upper() if m else "未標明座數"


def normalize_floor(raw_text: str) -> str:
    text = raw_text or ""
    if "高層" in text:
        return "高層"
    if "中層" in text:
        return "中層"
    if "低層" in text:
        return "低層"
    return "未標明樓層"


def compute_phash(img_url: str):
    if not img_url or not img_url.startswith("http"):
        return None
    try:
        resp = requests.get(img_url, headers=HEADERS, timeout=8)
        if resp.status_code == 200:
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            return imagehash.phash(img)
    except Exception:
        return None
    return None


def load_previous_snapshot() -> dict:
    prev_index = {}
    if not os.path.exists(DATA_PATH):
        return prev_index
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            old_data = json.load(f)
        for u in old_data.get("listings", []):
            key = f"{u.get('estate')}|{u.get('block')}|{u.get('area')}|{u.get('floor')}"
            prev_index[key] = u.get("min_price")
    except Exception as e:
        print(f"[Snapshot] 無法讀取舊數據：{e}")
    return prev_index


# ==========================================
# 一、抓取大埔區屋苑近期成交紀錄（中原 + 美聯雙源）
# ==========================================

def fetch_centaline_taipo_transactions(pages: int = 6) -> list:
    """從中原地產公開成交 API 抓取大埔區最新成交實呎價"""
    tx_records = []
    api_url = "https://hk.centanet.com/findproperty/api/Transaction/Search"
    for page in range(1, pages + 1):
        payload = {
            "scope": "All-Tai-Po_23-WS039",
            "page": page,
            "size": 30,
            "sort": "InsDate",
            "order": "Descending",
        }
        try:
            resp = requests.post(api_url, json=payload, headers=HEADERS, timeout=15)
            data = resp.json()
            items = data.get("data", [])
            if not items:
                break
            for item in items:
                price = float(item.get("price") or 0)
                area = float(item.get("nArea") or item.get("gArea") or 0)
                unit_price = float(item.get("nUnitPrice") or 0)
                if unit_price <= 0 and price > 0 and area > 0:
                    unit_price = price / area
                if unit_price <= 2000 or unit_price >= 80000:
                    continue

                raw_estate = item.get("estateName", "")
                estate, sub_dist = normalize_estate(raw_estate)
                tx_date = (item.get("insDate") or item.get("txDate") or "")[:10]

                tx_records.append({
                    "source": "中原成交",
                    "estate": estate,
                    "sub_district": sub_dist,
                    "unit_price": round(unit_price),
                    "tx_date": tx_date,
                })
            polite_sleep(1.3)
        except Exception as e:
            print(f"[Centaline TX] Page {page} error: {e}")
    return tx_records


def fetch_midland_taipo_transactions() -> list:
    """從美聯物業大埔區成交頁面提取近期成交實呎價作補充"""
    tx_records = []
    url = "https://www.midland.com.hk/zh-hk/list/transaction/%E6%96%B0%E7%95%8C-%E5%A4%A7%E5%9F%94-D-130ND30037"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        script = soup.find("script", id="__NEXT_DATA__")
        if not script:
            return tx_records
        next_data = json.loads(script.string)
        tx_list = (
            next_data.get("props", {})
            .get("pageProps", {})
            .get("initialState", {})
            .get("transaction", {})
            .get("list", [])
        )
        for item in tx_list:
            price = float(item.get("price") or 0)
            area = float(item.get("netArea") or 0)
            if price <= 0 or area <= 0:
                continue
            unit_price = price / area
            if unit_price <= 2000 or unit_price >= 80000:
                continue

            raw_estate = item.get("estateName", "")
            estate, sub_dist = normalize_estate(raw_estate)
            tx_date = (item.get("txDate") or "")[:10]

            tx_records.append({
                "source": "美聯成交",
                "estate": estate,
                "sub_district": sub_dist,
                "unit_price": round(unit_price),
                "tx_date": tx_date,
            })
    except Exception as e:
        print(f"[Midland TX] error: {e}")
    return tx_records


def build_estate_benchmarks(tx_records: list) -> tuple[dict, dict]:
    """按屋苑及大埔分區彙整近期平均成交呎價"""
    estate_prices = defaultdict(list)
    estate_latest = {}
    subdist_prices = defaultdict(list)

    for tx in tx_records:
        est = tx["estate"]
        sub = tx["sub_district"]
        up = tx["unit_price"]
        estate_prices[est].append(up)
        subdist_prices[sub].append(up)
        if tx["tx_date"] and tx["tx_date"] > estate_latest.get(est, ""):
            estate_latest[est] = tx["tx_date"]

    estate_bench = {}
    for est, prices in estate_prices.items():
        estate_bench[est] = {
            "avg_unit_price": round(sum(prices) / len(prices)),
            "tx_count": len(prices),
            "latest_tx_date": estate_latest.get(est, ""),
        }

    subdist_bench = {}
    for sub, prices in subdist_prices.items():
        subdist_bench[sub] = {
            "avg_unit_price": round(sum(prices) / len(prices)),
            "tx_count": len(prices),
        }

    return estate_bench, subdist_bench


# ==========================================
# 二、抓取大埔區在售放盤廣告（28Hse + 中原 + 美聯）
# ==========================================

def fetch_28hse_taipo(max_pages: int = 5) -> list:
    results = []
    for page in range(1, max_pages + 1):
        url = f"https://www.28hse.com/buy/a3/dg45/di45-103/p{page}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select(".property_item, .item.property_list_item")
            if not cards:
                break
            for card in cards:
                title_el = card.select_one("a.detail_page, .title a")
                price_el = card.select_one(".price, .green.price")
                img_el = card.select_one("img")
                if not title_el or not price_el:
                    continue

                title_text = title_el.get_text(" ", strip=True)
                href = title_el.get("href", "")
                full_url = href if href.startswith("http") else f"https://www.28hse.com{href}"

                card_text = card.get_text(" ", strip=True)
                price_match = re.search(r"([\d.]+)\s*萬", price_el.get_text())
                area_match = re.search(r"實\s*([\d,]+)\s*呎|([\d,]+)\s*呎", card_text)
                if not price_match or not area_match:
                    continue

                price_wan = float(price_match.group(1))
                area_sqft = int((area_match.group(1) or area_match.group(2)).replace(",", ""))
                if price_wan <= 0 or area_sqft <= 0:
                    continue

                estate, sub_dist = normalize_estate(title_text)
                is_owner = "業主" in card_text
                img_src = (img_el.get("data-src") or img_el.get("src") or "") if img_el else ""
                room_m = re.search(r"(\d房)", card_text)

                results.append({
                    "platform": "28Hse (業主盤)" if is_owner else "28Hse",
                    "estate": estate,
                    "sub_district": sub_dist,
                    "block": normalize_block(title_text),
                    "floor": normalize_floor(title_text),
                    "area": area_sqft,
                    "price_wan": price_wan,
                    "unit_price": round((price_wan * 10000) / area_sqft),
                    "rooms": room_m.group(1) if room_m else "未標明",
                    "url": full_url,
                    "image_url": img_src if img_src.startswith("http") and "nophoto" not in img_src else "",
                    "scraped_at": datetime.now(HKT).strftime("%H:%M"),
                })
            polite_sleep(1.6)
        except Exception as e:
            print(f"[28Hse] Page {page} error: {e}")
    return results


def fetch_centaline_taipo(pages: int = 5) -> list:
    results = []
    api_url = "https://hk.centanet.com/findproperty/api/Post/Search"
    for page in range(1, pages + 1):
        payload = {
            "postType": "Sale",
            "scope": "All-Tai-Po_23-WS039",
            "page": page,
            "size": 24,
            "sort": "Default",
        }
        try:
            resp = requests.post(api_url, json=payload, headers=HEADERS, timeout=15)
            data = resp.json()
            items = data.get("data", [])
            if not items:
                break
            for item in items:
                price_wan = round(item.get("price", 0) / 10000, 1)
                area_sqft = int(item.get("nArea") or item.get("gArea") or 0)
                if price_wan <= 0 or area_sqft <= 0:
                    continue

                raw_estate = item.get("estateName", "")
                building = item.get("buildingName", "")
                estate, sub_dist = normalize_estate(f"{raw_estate}{building}")
                post_id = item.get("id", "")
                photos = item.get("photos") or []
                first_photo = photos[0].get("url", "") if photos and isinstance(photos[0], dict) else ""

                results.append({
                    "platform": "中原地產",
                    "estate": estate,
                    "sub_district": sub_dist,
                    "block": normalize_block(building or raw_estate),
                    "floor": normalize_floor(item.get("floor", "")),
                    "area": area_sqft,
                    "price_wan": price_wan,
                    "unit_price": round((price_wan * 10000) / area_sqft),
                    "rooms": f"{item.get('bedroomCount', '')}房" if item.get("bedroomCount") else "未標明",
                    "url": f"https://hk.centanet.com/findproperty/detail/{post_id}",
                    "image_url": first_photo,
                    "scraped_at": datetime.now(HKT).strftime("%H:%M"),
                })
            polite_sleep(1.4)
        except Exception as e:
            print(f"[Centaline] Page {page} error: {e}")
    return results


def fetch_midland_taipo() -> list:
    results = []
    url = "https://www.midland.com.hk/zh-hk/list/buy/%E6%96%B0%E7%95%8C-%E5%A4%A7%E5%9F%94-D-130ND30037"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        script = soup.find("script", id="__NEXT_DATA__")
        if not script:
            return results
        next_data = json.loads(script.string)
        props_list = (
            next_data.get("props", {})
            .get("pageProps", {})
            .get("initialState", {})
            .get("search", {})
            .get("list", [])
        )
        for item in props_list:
            price_wan = round(float(item.get("price", 0)) / 10000, 1)
            area_sqft = int(item.get("netArea") or 0)
            if price_wan <= 0 or area_sqft <= 0:
                continue
            raw_estate = item.get("estateName", "")
            bldg = item.get("buildingName", "")
            estate, sub_dist = normalize_estate(f"{raw_estate}{bldg}")
            tx_id = item.get("id", "")

            results.append({
                "platform": "美聯物業",
                "estate": estate,
                "sub_district": sub_dist,
                "block": normalize_block(bldg),
                "floor": normalize_floor(item.get("floorLevel", "")),
                "area": area_sqft,
                "price_wan": price_wan,
                "unit_price": round((price_wan * 10000) / area_sqft),
                "rooms": f"{item.get('bedroom', '')}房" if item.get("bedroom") else "未標明",
                "url": f"https://www.midland.com.hk/zh-hk/property/{tx_id}",
                "image_url": item.get("mainPhoto", ""),
                "scraped_at": datetime.now(HKT).strftime("%H:%M"),
            })
    except Exception as e:
        print(f"[Midland] error: {e}")
    return results


# ==========================================
# 三、雙軌認盤 + 成交均價折讓計算 + 12小時快照比對
# ==========================================

def merge_and_evaluate(raw_items: list, prev_index: dict, estate_bench: dict, subdist_bench: dict) -> list:
    for item in raw_items:
        item["phash"] = compute_phash(item.get("image_url", ""))

    master_units = []

    for item in raw_items:
        matched_unit = None
        match_reason = ""
        match_track = "single"

        for unit in master_units:
            if unit["estate"] != item["estate"]:
                continue
            if unit["block"] != item["block"]:
                continue
            if abs(unit["area"] - item["area"]) > 1:
                continue

            # 軌道 A：有相盤圖片指紋比對
            if unit.get("phash") is not None and item.get("phash") is not None:
                dist = unit["phash"] - item["phash"]
                if dist <= 5:
                    matched_unit = unit
                    match_track = "photo"
                    match_reason = f"圖片裝修指紋吻合 (pHash距離={dist})"
                    break

            # 軌道 B：無相盤文字 + 叫價精準鎖定
            same_floor = (
                unit["floor"] == item["floor"]
                or "未標明" in (unit["floor"], item["floor"])
            )
            price_diff_pct = abs(unit["min_price"] - item["price_wan"]) / max(unit["min_price"], 1)
            if same_floor and price_diff_pct <= 0.02:
                matched_unit = unit
                match_track = "text_price"
                match_reason = f"屋苑/座數/實呎/樓層一致，跨網叫價誤差 {price_diff_pct*100:.1f}%"
                break

        offer = {
            "platform": item["platform"],
            "price_wan": item["price_wan"],
            "unit_price": item["unit_price"],
            "url": item["url"],
            "has_photo": bool(item.get("image_url")),
            "scraped_at": item["scraped_at"],
        }

        if matched_unit:
            existing_platforms = [o["platform"] for o in matched_unit["offers"]]
            if item["platform"] not in existing_platforms:
                matched_unit["offers"].append(offer)
                matched_unit["offers"].sort(key=lambda x: x["price_wan"])
                matched_unit["min_price"] = matched_unit["offers"][0]["price_wan"]
                matched_unit["max_price"] = matched_unit["offers"][-1]["price_wan"]
                matched_unit["min_unit_price"] = matched_unit["offers"][0]["unit_price"]
                matched_unit["spread_wan"] = round(matched_unit["max_price"] - matched_unit["min_price"], 1)
                if match_track == "photo" or matched_unit["match_track"] == "single":
                    matched_unit["match_track"] = match_track
                    matched_unit["match_reason"] = match_reason
                if not matched_unit["cover_image"] and item.get("image_url"):
                    matched_unit["cover_image"] = item["image_url"]
        else:
            master_units.append({
                "id": f"TP-{len(master_units)+1:04d}",
                "estate": item["estate"],
                "sub_district": item["sub_district"],
                "block": item["block"],
                "floor": item["floor"],
                "area": item["area"],
                "rooms": item["rooms"],
                "cover_image": item.get("image_url", ""),
                "phash": item.get("phash"),
                "min_price": item["price_wan"],
                "max_price": item["price_wan"],
                "min_unit_price": item["unit_price"],
                "spread_wan": 0.0,
                "match_track": "single",
                "match_reason": "單一平台放盤",
                "offers": [offer],
            })

    for u in master_units:
        u.pop("phash", None)

        # 1. 對比 12 小時前快照
        snap_key = f"{u['estate']}|{u['block']}|{u['area']}|{u['floor']}"
        if prev_index and snap_key not in prev_index:
            u["is_new_12h"] = True
            u["price_change_wan"] = 0.0
        elif snap_key in prev_index:
            u["is_new_12h"] = False
            u["price_change_wan"] = round(u["min_price"] - prev_index[snap_key], 1)
        else:
            u["is_new_12h"] = False
            u["price_change_wan"] = 0.0

        # 2. 對比屋苑近期平均成交呎價（優先用同屋苑近期成交，不足則退回所屬大埔分區成交均價）
        est_info = estate_bench.get(u["estate"])
        sub_info = subdist_bench.get(u["sub_district"])

        if est_info and est_info["avg_unit_price"] > 0:
            avg_tx = est_info["avg_unit_price"]
            u["estate_avg_unit_price"] = avg_tx
            u["estate_tx_count"] = est_info["tx_count"]
            u["benchmark_scope"] = "estate"
            u["vs_market_pct"] = round(((u["min_unit_price"] - avg_tx) / avg_tx) * 100, 1)
        elif sub_info and sub_info["avg_unit_price"] > 0:
            avg_tx = sub_info["avg_unit_price"]
            u["estate_avg_unit_price"] = avg_tx
            u["estate_tx_count"] = sub_info["tx_count"]
            u["benchmark_scope"] = "sub_district"
            u["vs_market_pct"] = round(((u["min_unit_price"] - avg_tx) / avg_tx) * 100, 1)
        else:
            u["estate_avg_unit_price"] = None
            u["estate_tx_count"] = 0
            u["benchmark_scope"] = "none"
            u["vs_market_pct"] = None

    # 預設排序：優先展示「跨網重疊盤」與「低於成交均價幅度最大」的筍盤
    master_units.sort(
        key=lambda x: (
            -len(x["offers"]),
            x["vs_market_pct"] if x["vs_market_pct"] is not None else 999,
            x["min_price"],
        )
    )
    return master_units


def main():
    now_hkt = datetime.now(HKT)
    next_hkt = now_hkt + timedelta(hours=12)
    print(f"[{now_hkt.strftime('%Y-%m-%d %H:%M HKT')}] 啟動大埔區 12 小時全網放盤 + 成交均價採集...")

    prev_index = load_previous_snapshot()

    # 抓取近期成交並計算屋苑平均呎價
    tx_centa = fetch_centaline_taipo_transactions(pages=6)
    tx_midland = fetch_midland_taipo_transactions()
    all_tx = tx_centa + tx_midland
    estate_bench, subdist_bench = build_estate_benchmarks(all_tx)
    print(f"成交數據：共抓取 {len(all_tx)} 宗大埔近期成交，建立 {len(estate_bench)} 個屋苑均價基準")

    # 抓取在售放盤廣告
    raw_28hse = fetch_28hse_taipo(max_pages=5)
    raw_centa = fetch_centaline_taipo(pages=5)
    raw_midland = fetch_midland_taipo()
    all_raw = raw_28hse + raw_centa + raw_midland

    merged = merge_and_evaluate(all_raw, prev_index, estate_bench, subdist_bench)
    below_market_count = sum(
        1 for u in merged if u.get("vs_market_pct") is not None and u["vs_market_pct"] < 0
    )

    output = {
        "district": "大埔區",
        "interval_hours": 12,
        "updated_at": now_hkt.strftime("%Y-%m-%d %H:%M HKT"),
        "next_scheduled_at": next_hkt.strftime("%Y-%m-%d %H:%M HKT"),
        "stats": {
            "raw_total": len(all_raw),
            "merged_units": len(merged),
            "cross_platform_matches": sum(1 for u in merged if len(u["offers"]) > 1),
            "below_market_bargains": below_market_count,
            "tx_samples_count": len(all_tx),
            "new_in_12h": sum(1 for u in merged if u.get("is_new_12h")),
            "price_drops_in_12h": sum(1 for u in merged if u.get("price_change_wan", 0) < 0),
            "sources": {
                "28Hse": len(raw_28hse),
                "中原地產": len(raw_centa),
                "美聯物業": len(raw_midland),
            },
        },
        "estate_benchmarks": estate_bench,
        "listings": merged,
    }

    os.makedirs("data", exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"完成！輸出 {len(merged)} 個大埔合併單位（其中 {below_market_count} 個低於近期成交均價）")


if __name__ == "__main__":
    main()
