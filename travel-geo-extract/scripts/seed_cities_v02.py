"""One-shot data migration: seed cities for the 33 v0.2 countries and re-pin
the headline city alias from country root to the new city id.

Run once after the initial Excel import. Safe to re-run (skips existing entries).

Usage:
    python scripts/seed_cities_v02.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# (city_id, name, en, country_id, headline_aliases)
# headline_aliases = list of alias strings already in aliases.csv (pointed at country)
# that should be re-pinned to this new city. Leave [] to only add the city.
CITY_SEED: list[tuple[str, str, str, str, list[str]]] = [
    # 義大利
    ("IT-ROM", "羅馬", "Rome", "IT", ["羅馬"]),
    ("IT-FLR", "佛羅倫斯", "Florence", "IT", ["佛羅倫斯"]),
    ("IT-MIL", "米蘭", "Milan", "IT", ["米蘭"]),
    ("IT-VCE", "威尼斯", "Venice", "IT", []),  # 威尼斯 alias 已 ambig (與 IT 並存)
    ("IT-NAP", "那不勒斯", "Naples", "IT", []),
    # 法國
    ("FR-PAR", "巴黎", "Paris", "FR", ["巴黎"]),
    ("FR-NCE", "尼斯", "Nice", "FR", ["尼斯"]),
    ("FR-LYS", "里昂", "Lyon", "FR", []),
    ("FR-MRS", "馬賽", "Marseille", "FR", []),
    # 瑞士
    ("CH-ZRH", "蘇黎世", "Zurich", "CH", ["蘇黎世"]),
    ("CH-LCN", "琉森", "Lucerne", "CH", ["琉森"]),
    ("CH-INT", "因特拉肯", "Interlaken", "CH", ["因特拉肯"]),
    ("CH-BRN", "伯恩", "Bern", "CH", ["伯恩"]),
    ("CH-ZRT", "策馬特", "Zermatt", "CH", ["策馬特"]),
    # 奧地利
    ("AT-VIE", "維也納", "Vienna", "AT", ["維也納"]),
    ("AT-SZG", "薩爾斯堡", "Salzburg", "AT", []),
    ("AT-HST", "哈修塔特", "Hallstatt", "AT", ["哈修塔特"]),
    # 德國
    ("DE-BER", "柏林", "Berlin", "DE", ["柏林"]),
    ("DE-MUC", "慕尼黑", "Munich", "DE", ["慕尼黑"]),
    ("DE-FRA", "法蘭克福", "Frankfurt", "DE", ["法蘭克福"]),
    # 英國
    ("GB-LON", "倫敦", "London", "GB", ["倫敦"]),
    ("GB-EDI", "愛丁堡", "Edinburgh", "GB", []),
    ("GB-OXF", "牛津", "Oxford", "GB", ["牛津"]),
    ("GB-CMB", "劍橋", "Cambridge", "GB", []),
    # 荷蘭
    ("NL-AMS", "阿姆斯特丹", "Amsterdam", "NL", ["阿姆斯特丹"]),
    # 西班牙
    ("ES-BCN", "巴塞隆納", "Barcelona", "ES", []),
    ("ES-MAD", "馬德里", "Madrid", "ES", []),
    ("ES-GRX", "格拉納達", "Granada", "ES", []),
    # 葡萄牙
    ("PT-LIS", "里斯本", "Lisbon", "PT", []),
    ("PT-OPO", "波多", "Porto", "PT", []),
    # 希臘
    ("GR-ATH", "雅典", "Athens", "GR", []),
    ("GR-JTR", "聖托里尼", "Santorini", "GR", []),
    ("GR-MYK", "米克諾斯", "Mykonos", "GR", []),
    # 捷克
    ("CZ-PRG", "布拉格", "Prague", "CZ", []),
    ("CZ-CKR", "庫倫洛夫", "Cesky Krumlov", "CZ", []),
    # 匈牙利
    ("HU-BUD", "布達佩斯", "Budapest", "HU", []),
    # 克羅埃西亞
    ("HR-ZAG", "札格雷布", "Zagreb", "HR", []),
    ("HR-DBV", "杜布羅夫尼克", "Dubrovnik", "HR", []),
    # 冰島
    ("IS-REK", "雷克雅維克", "Reykjavik", "IS", []),
    # 芬蘭
    ("FI-HEL", "赫爾辛基", "Helsinki", "FI", []),
    ("FI-RVN", "羅瓦涅米", "Rovaniemi", "FI", []),
    # 挪威
    ("NO-OSL", "奧斯陸", "Oslo", "NO", []),
    ("NO-BGO", "卑爾根", "Bergen", "NO", []),
    ("NO-TOS", "特羅姆瑟", "Tromso", "NO", []),
    # 埃及
    ("EG-CAI", "開羅", "Cairo", "EG", []),
    ("EG-LXR", "路克索", "Luxor", "EG", []),
    # 摩洛哥
    ("MA-RAK", "馬拉喀什", "Marrakech", "MA", []),
    ("MA-CMN", "卡薩布蘭加", "Casablanca", "MA", []),
    ("MA-FEZ", "菲斯", "Fes", "MA", []),
    ("MA-CHF", "舍夫沙萬", "Chefchaouen", "MA", []),
    # 南非
    ("ZA-CPT", "開普敦", "Cape Town", "ZA", []),
    ("ZA-JNB", "約翰尼斯堡", "Johannesburg", "ZA", []),
    # 澳洲
    ("AU-SYD", "雪梨", "Sydney", "AU", []),
    ("AU-MEL", "墨爾本", "Melbourne", "AU", []),
    ("AU-BNE", "布里斯本", "Brisbane", "AU", []),
    ("AU-CNS", "凱恩斯", "Cairns", "AU", []),
    # 紐西蘭
    ("NZ-AKL", "奧克蘭", "Auckland", "NZ", []),
    ("NZ-ZQN", "皇后鎮", "Queenstown", "NZ", []),
    ("NZ-CHC", "基督城", "Christchurch", "NZ", []),
    # 美國
    ("US-NYC", "紐約", "New York", "US", []),
    ("US-LAX", "洛杉磯", "Los Angeles", "US", []),
    ("US-SFO", "舊金山", "San Francisco", "US", []),
    ("US-LAS", "拉斯維加斯", "Las Vegas", "US", []),
    ("US-MCO", "奧蘭多", "Orlando", "US", []),
    ("US-HNL", "夏威夷", "Hawaii", "US", []),
    # 加拿大
    ("CA-YYZ", "多倫多", "Toronto", "CA", []),
    ("CA-YVR", "溫哥華", "Vancouver", "CA", []),
    ("CA-YYC", "班夫", "Banff", "CA", []),
    ("CA-YZF", "黃刀鎮", "Yellowknife", "CA", []),
    # 墨西哥
    ("MX-MEX", "墨西哥城", "Mexico City", "MX", []),
    ("MX-CUN", "坎昆", "Cancun", "MX", []),
    # 新加坡 (city-state)
    ("SG-SIN", "新加坡市", "Singapore City", "SG", []),
    # 馬來西亞
    ("MY-KUL", "吉隆坡", "Kuala Lumpur", "MY", ["吉隆坡"]),
    ("MY-PEN", "檳城", "Penang", "MY", []),
    ("MY-MKZ", "馬六甲", "Malacca", "MY", ["馬六甲"]),
    ("MY-BKI", "亞庇", "Kota Kinabalu", "MY", ["亞庇", "沙巴"]),
    # 香港 (Hong Kong - districts as cities for simplicity)
    ("HK-KLN", "九龍", "Kowloon", "HK", []),
    ("HK-HKI", "港島", "Hong Kong Island", "HK", []),
    # 澳門
    ("MO-MAC", "澳門半島", "Macau Peninsula", "MO", []),
    ("MO-COT", "路氹", "Cotai", "MO", ["路氹"]),
    # 柬埔寨
    ("KH-REP", "暹粒", "Siem Reap", "KH", ["暹粒"]),
    ("KH-PNH", "金邊", "Phnom Penh", "KH", ["金邊"]),
    # 土耳其
    ("TR-IST", "伊斯坦堡", "Istanbul", "TR", ["伊斯坦堡"]),
    ("TR-NAV", "卡帕多奇亞", "Cappadocia", "TR", ["卡帕多奇亞"]),
    ("TR-DNZ", "棉堡", "Pamukkale", "TR", ["棉堡"]),
    # 阿聯酋
    ("AE-DXB", "杜拜", "Dubai", "AE", ["杜拜"]),
    ("AE-AUH", "阿布達比", "Abu Dhabi", "AE", ["阿布達比"]),
    # 印度
    ("IN-DEL", "德里", "Delhi", "IN", []),
    ("IN-BOM", "孟買", "Mumbai", "IN", []),
    ("IN-AGR", "阿格拉", "Agra", "IN", []),
    ("IN-JAI", "齋浦爾", "Jaipur", "IN", []),
    # 尼泊爾
    ("NP-KTM", "加德滿都", "Kathmandu", "NP", []),
    ("NP-PKR", "波卡拉", "Pokhara", "NP", []),
]


def main() -> None:
    # 1. add cities to locations.csv (skip if id exists)
    loc_path = DATA_DIR / "locations.csv"
    with loc_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        existing_ids = {row["id"] for row in reader}
        f.seek(0)
        loc_fields = next(csv.reader(f))

    new_locs = []
    for cid, name, en, country_id, _ in CITY_SEED:
        if cid in existing_ids:
            continue
        new_locs.append({
            "id": cid, "name": name, "en_name": en, "ja_name": "",
            "type": "city", "parent_id": country_id,
            "sort_order": "10", "active": "1",
            "notes": "v0.2 seed",
        })

    if new_locs:
        with loc_path.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=loc_fields, quoting=csv.QUOTE_MINIMAL)
            w.writerows(new_locs)
    print(f"+ {len(new_locs)} cities added to locations.csv")

    # 2. re-pin headline aliases from country root to new city id
    alias_path = DATA_DIR / "aliases.csv"
    with alias_path.open(encoding="utf-8-sig", newline="") as f:
        alias_rows = list(csv.DictReader(f))
        f.seek(0)
        alias_fields = next(csv.reader(f))

    repins = 0
    headline_map: dict[str, tuple[str, str]] = {}  # alias -> (country_id, new_city_id)
    for cid, _, _, country_id, headlines in CITY_SEED:
        for h in headlines:
            headline_map[h] = (country_id, cid)

    for row in alias_rows:
        key = (row["alias"], row["location_id"])
        if row["alias"] in headline_map:
            country_id, new_city_id = headline_map[row["alias"]]
            if row["location_id"] == country_id:
                row["location_id"] = new_city_id
                repins += 1

    with alias_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=alias_fields, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(alias_rows)
    print(f"+ {repins} aliases re-pinned to city")

    # 3. summary
    cities_per_country: dict[str, int] = {}
    for cid, _, _, country, _ in CITY_SEED:
        cities_per_country[country] = cities_per_country.get(country, 0) + 1
    print(f"\nCity counts per v0.2 country:")
    for k in sorted(cities_per_country.keys()):
        print(f"  {k}: {cities_per_country[k]}")


if __name__ == "__main__":
    main()
