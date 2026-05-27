# update_kb.py CLI 使用範例

所有知識庫修改直接操作 `data/geo_kb.db`（SQLite），不需要 CSV。

---

## 查詢

```bash
# 列出所有國家
python scripts/update_kb.py list --type country

# 列出所有地區
python scripts/update_kb.py list --type region

# 列出所有城市
python scripts/update_kb.py list --type city

# 列出所有別名
python scripts/update_kb.py list --type alias

# 列出所有地標
python scripts/update_kb.py list --type landmark

# 列出所有消歧規則
python scripts/update_kb.py list --type rule

# 列出所有角色關鍵字
python scripts/update_kb.py list --type role

# 列出所有弱信號
python scripts/update_kb.py list --type weak

# 搜尋（跨所有表）
python scripts/update_kb.py find --term 高山
python scripts/update_kb.py find --term JP-KTO
python scripts/update_kb.py find --term 威尼斯
```

---

## 新增地點

```bash
# 新增國家
python scripts/update_kb.py add-location \
  --id GR \
  --name 希臘 \
  --en-name Greece \
  --type country

# 新增地區（parent 指向國家）
python scripts/update_kb.py add-location \
  --id JP-SHK \
  --name 四國 \
  --en-name Shikoku \
  --type region \
  --parent JP

# 新增城市（parent 指向地區或國家）
python scripts/update_kb.py add-location \
  --id JP-SHK-MTS \
  --name 松山 \
  --en-name Matsuyama \
  --type city \
  --parent JP-SHK
```

---

## 新增別名

```bash
# 正式名稱
python scripts/update_kb.py add-alias \
  --alias 東瀛 \
  --location JP \
  --type nickname

# 英文名
python scripts/update_kb.py add-alias \
  --alias Shikoku \
  --location JP-SHK \
  --type en

# 歧義別名（需搭配 disambig rule）
python scripts/update_kb.py add-alias \
  --alias 松山 \
  --location JP-SHK-MTS \
  --type ambig \
  --confidence 0.5
```

---

## 新增消歧規則

```bash
# 「松山」在日本上下文 → 日本松山市
python scripts/update_kb.py add-rule \
  --term 松山 \
  --context "日本|四國|愛媛|道後" \
  --resolution JP-SHK-MTS \
  --priority 90 \
  --reason "日本愛媛縣松山市"

# 「松山」在機場上下文 → 排除（台北松山機場）
python scripts/update_kb.py add-rule \
  --term 松山 \
  --context "機場|出發|航班|松山機場" \
  --resolution SKIP \
  --priority 100 \
  --reason "台北松山機場，非目的地"

# 「長城」不帶中國上下文 → 排除（可能是比喻）
python scripts/update_kb.py add-rule \
  --term 長城 \
  --context "萬里|北京|八達嶺|居庸關" \
  --resolution CN-NOR-BJS \
  --priority 80 \
  --reason "明確指北京長城"
```

---

## 新增地標

```bash
# 景點
python scripts/update_kb.py add-landmark \
  --name 道後溫泉 \
  --city JP-SHK-MTS \
  --type onsen

# 機場（alias 用 IATA code）
python scripts/update_kb.py add-landmark \
  --name 松山機場 \
  --alias "TSA|臺北松山" \
  --city TW-TPE \
  --type airport

# 建築
python scripts/update_kb.py add-landmark \
  --name 晴空塔 \
  --alias "Skytree|天空樹" \
  --city JP-KTO-TYO \
  --type building
```

---

## 新增角色關鍵字

```bash
# 出發動詞
python scripts/update_kb.py add-rolekw \
  --keyword "啟程" \
  --role departure \
  --window 10 \
  --direction left

# 目的地動詞
python scripts/update_kb.py add-rolekw \
  --keyword "暢遊" \
  --role destination \
  --window 10 \
  --direction right

# 需要 anchor 的關鍵字
python scripts/update_kb.py add-rolekw \
  --keyword "報到" \
  --role departure \
  --window 10 \
  --direction left \
  --requires-context "機場|航廈|碼頭"
```

---

## 新增弱信號

```bash
python scripts/update_kb.py add-weak \
  --signal "環球影城" \
  --naive "美國/日本任一" \
  --possible "美國、日本、新加坡" \
  --strong "大阪:USJ/日本環球;新加坡:聖淘沙;美國:好萊塢/奧蘭多" \
  --guidance "必須搭配城市名或園區全名"
```

---

## 刪除

```bash
# 刪除地點
python scripts/update_kb.py remove --table locations --key JP-SHK-MTS

# 刪除別名（composite key: alias,location_id）
python scripts/update_kb.py remove --table aliases --key "東瀛,JP"

# 刪除地標
python scripts/update_kb.py remove --table landmarks --key 道後溫泉

# 刪除弱信號
python scripts/update_kb.py remove --table weak_signals --key 環球影城
```

---

## 驗證

```bash
# 驗證 SQLite 完整性
python scripts/validate_kb.py

# 跑所有測試 case
python scripts/extract.py test
```
