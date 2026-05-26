"""One-shot data migration: re-pin landmarks from country root to city.

114 landmarks were initially imported at country level (location_id = 'JP'/'IT' etc).
This script applies a curated mapping to put each at its real city. Landmarks
without a v0.2 city are left at country root with `notes` updated.

Run once. Safe to re-run (skips already-migrated entries).
"""

from __future__ import annotations

import csv
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# landmark_name -> new_city_id  (None = no v0.2 city, keep at country root)
REPIN: dict[str, str | None] = {
    # AE (杜拜/阿布達比)
    "哈里發塔": "AE-DXB", "帆船飯店": "AE-DXB", "棕櫚島": "AE-DXB",
    "Dubai Mall": "AE-DXB", "謝赫扎耶德大清真寺": "AE-AUH",
    # AT
    "美泉宮": "AT-VIE", "霍夫堡": "AT-VIE", "薩爾斯堡": "AT-SZG", "多瑙河": "AT-VIE",
    # AU
    "黃金海岸": "AU-BNE", "雪梨港灣大橋": "AU-SYD",
    # CA
    "尼加拉瀑布": "CA-YYZ", "露易絲湖": "CA-YYC",
    # CH
    "卡貝爾橋": "CH-LCN", "萊茵瀑布": "CH-ZRH",
    # CN: 峨眉山 in 四川 — no v0.2 city, keep CN
    "峨眉山": None,
    # CZ
    "查理大橋": "CZ-PRG", "布拉格城堡": "CZ-PRG",
    # DE
    "新天鵝堡": "DE-MUC", "海德堡": None, "科隆大教堂": None,
    # EG
    "吉薩金字塔": "EG-CAI", "尼羅河": "EG-CAI",
    # ES
    "奎爾公園": "ES-BCN", "普拉多美術館": "ES-MAD", "阿爾罕布拉宮": "ES-GRX",
    # FR
    "艾菲爾鐵塔": "FR-PAR", "羅浮宮": "FR-PAR", "凡爾賽宮": "FR-PAR",
    "塞納河": "FR-PAR", "聖米歇爾山": None, "蔚藍海岸": "FR-NCE",
    # GB
    "倫敦塔橋": "GB-LON", "白金漢宮": "GB-LON", "大英博物館": "GB-LON",
    "溫莎城堡": "GB-LON", "劍橋": "GB-CMB", "愛丁堡": "GB-EDI",
    # GR
    "衛城": "GR-ATH", "帕德嫩神廟": "GR-ATH", "藍頂教堂": "GR-JTR",
    # HK
    "太平山": "HK-HKI", "香港迪士尼": "HK-HKI",
    "海洋公園": "HK-HKI", "大嶼山": "HK-HKI",
    # HR
    "十六湖": "HR-ZAG", "戴克里先宮": None,
    # HU
    "漁人堡": "HU-BUD",
    # ID
    "巴里島": "ID-DPS", "藍夢島": "ID-DPS",
    # IN: 恆河 流經多城 — keep IN
    "恆河": None,
    # IS
    "藍湖": "IS-REK", "黃金瀑布": "IS-REK", "黑沙灘": "IS-REK", "傑古沙龍冰河湖": "IS-REK",
    # IT
    "聖彼得大教堂": "IT-ROM", "聖母百花大教堂": "IT-FLR",
    "比薩斜塔": "IT-FLR", "嘆息橋": "IT-VCE",
    # JP
    "美麗海水族館": "JP-OKW-NHA",
    # KH
    "巴戎寺": "KH-REP", "塔普倫寺": "KH-REP",
    "女王宮": "KH-REP", "洞里薩湖": "KH-REP",
    # KR
    "愛寶樂園": "KR-SEL", "牛島": "KR-CJU",
    # MA
    "藍色山城": "MA-CHF", "哈桑二世清真寺": "MA-CMN",
    # MO
    "媽閣廟": "MO-MAC", "永利皇宮": "MO-COT", "金沙城": "MO-COT",
    # MX
    "墨西哥城": "MX-MEX", "太陽金字塔": "MX-MEX",
    # MY
    "雙子星塔": "MY-KUL", "檳城": "MY-PEN", "神山": "MY-BKI",
    # NL
    "梵谷博物館": "NL-AMS", "運河": "NL-AMS",
    # NP
    "費瓦湖": "NP-PKR",
    # NZ
    "庫克山": "NZ-CHC", "蒂卡波湖": "NZ-CHC", "好牧羊人教堂": "NZ-CHC",
    "南島": "NZ-CHC", "北島": "NZ-AKL",
    # PH
    "巧克力山": "PH-CEB", "愛妮島": "PH-PPS", "科隆島": "PH-PPS",
    # PT
    "貝倫塔": "PT-LIS", "杜羅河": "PT-OPO", "佩納宮": "PT-LIS",
    # SG
    "魚尾獅": "SG-SIN", "濱海灣金沙": "SG-SIN",
    "濱海灣花園": "SG-SIN", "USS": "SG-SIN",
    # TH
    "臥佛寺": "TH-BKK", "昭披耶河": "TH-BKK",
    # TR
    "伊斯坦堡": "TR-IST", "藍色清真寺": "TR-IST",
    "托普卡匹皇宮": "TR-IST", "棉堡": "TR-DNZ",
    # TW
    "綠島": "TW-TTT",
    # US
    "中央公園": "US-NYC", "好萊塢": "US-LAX",
    "舊金山": "US-SFO", "金門大橋": "US-SFO",
    # VN
    "Grand World": "VN-PQC", "Vinpearl": "VN-PQC", "Vinpearl Safari": "VN-PQC",
    "黃金橋": "VN-DAD", "美溪沙灘": "VN-DAD",
    # ZA
    "桌山": "ZA-CPT", "企鵝灘": "ZA-CPT",
    "約翰尼斯堡": "ZA-JNB", "克魯格國家公園": "ZA-JNB",
}


def main() -> None:
    path = DATA_DIR / "landmark_index.csv"
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
        f.seek(0)
        fields = next(csv.reader(f))

    repinned = 0
    kept = 0
    skipped = 0
    valid_ids: set[str] = set()
    # validate city ids exist in locations.csv
    with (DATA_DIR / "locations.csv").open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            valid_ids.add(r["id"])

    for row in rows:
        target = REPIN.get(row["landmark"])
        if target is None:
            continue  # not in our list OR explicitly keeps country root
        if row["location_id"] == target:
            skipped += 1
            continue
        if target not in valid_ids:
            print(f"  ! invalid target city {target} for {row['landmark']}; skip")
            continue
        old = row["location_id"]
        row["location_id"] = target
        # keep country_id correct
        row["country_id"] = target.split("-")[0]
        if "從 Excel 匯入" in row.get("notes", ""):
            row["notes"] = row["notes"].replace("掛國家根，待細化到城市", "v0.2 重 pin 到城市")
        print(f"  {row['landmark']}  {old} -> {target}")
        repinned += 1

    # mark items kept at country root with explicit note (the None entries)
    for row in rows:
        lm = row["landmark"]
        if lm in REPIN and REPIN[lm] is None:
            if "保留國家層" not in row.get("notes", ""):
                row["notes"] = (row.get("notes", "") + " | 保留國家層 (v0.2 暫無對應 city)").lstrip(" |")
                kept += 1

    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(rows)

    print(f"\nrepinned: {repinned}")
    print(f"kept at country (annotated): {kept}")
    print(f"already correct (skipped): {skipped}")


if __name__ == "__main__":
    main()
