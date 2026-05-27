---
name: travel-geo-extract
description: 從旅遊 DM、行程表、行銷文案中抽取「國家 / 地區 / 城市 / 景點」標籤，並維護國家-地區對應表。支援 .txt/.md/.docx/.pdf 與圖片 OCR 輸入。針對「高山火車」、「桃園機場集合」、「東方威尼斯」「越南威尼斯」「澳門威尼斯人」等歧義/角色/比喻/弱信號自動消解。觸發詞：旅遊地名抽取、旅遊DM抽取、行程抽取、旅遊文案打標、country tagging、travel NER、大都會旅遊、ddv 標籤、把這份 DM 轉成標籤、幫我抽國家地區、強弱信號判定、識別國家。
metadata:
  version: 0.2.0
  author: 大都會旅遊 (ddv.com.tw)
  scope: 41 國 — 國內(台灣)、東南亞(泰越菲印新馬柬)、東北亞(日韓)、港澳、中國 4 大區、中東(土耳其、阿聯酋)、歐洲 16 國、非洲(埃及、摩洛哥、南非)、大洋洲(澳紐)、美洲(美加墨)、南亞(印度、尼泊爾)
  master_file: C:\Users\User\Desktop\2d3d\旅遊表\旅遊DM國家關鍵字排除Mapping表.xlsx
---

# travel-geo-extract

從任何旅遊文本抽取結構化的 `{國家, 地區, 城市, 景點}` 標籤，並能維護背後的國家-地區知識表。

## 何時使用此 Skill

當用戶說以下任何一種，**立刻啟用本 skill**：

- 「幫我抽這份 DM 的國家地區」 / 「打標籤」 / 「country tagging」
- 「這個行程是去哪個國家」 / 「分類這份文案」
- 「新增地點到對應表」 / 「更新國家表」 / 「修改別名」
- 提供 `.txt / .md / .docx / .pdf / .jpg / .png` 並要求識別地名
- 提及大都會旅遊（ddv.com.tw）的 DM、行程、產品分類

**不要使用本 skill**：
- 一般地理問答（「日本首都是哪裡」） → 直接回答
- 行銷文案撰寫 → 用 Content Creator agent
- 旅遊景點推薦 → 一般對話

## 知識表結構

**權威主檔**是 Excel：`C:\Users\User\Desktop\2d3d\旅遊表\旅遊DM國家關鍵字排除Mapping表.xlsx`
業務人員直接編輯該 Excel（含狀態、維護日期、人工覆核條件等管理欄位），
然後跑 `python scripts/import_xlsx.py` 同步到下列 6 張 CSV：

| 檔案 | 用途 | 關鍵欄位 |
|---|---|---|
| `data/locations.csv` | 階層式地點主表 | id, name, type, parent_id |
| `data/aliases.csv` | 別名/英日韓/舊名 → location_id | alias, location_id, alias_type |
| `data/disambig_rules.csv` | 同名歧義消解（如「高山」） | ambiguous_term, context_regex, resolution |
| `data/landmark_index.csv` | 景點/機場 → 所屬城市 | landmark, location_id, type |
| `data/role_keywords.csv` | 角色判定關鍵詞（集合/轉機/比喻） | keyword, role, direction, weight |
| `data/weak_signals.csv` | 跨國弱信號詞庫（迪士尼、威尼斯、極光、馬爾地夫級…） | weak_signal, naive_country, possible_countries, required_strong, guidance |

`type` 階層：`country` → `region`（可缺）→ `city`。景點不入 locations，放 landmark_index。

`id` 命名：`國家2碼-地區3碼-城市3碼`，例 `JP-KTO-TYO`（日本-關東-東京）、`TW-TPE`（台灣-台北，無大區）、`CN-EAS-SHA`（中國-華東-上海）。

## 抽取流程（7 步）

### Step 1 — 輸入正規化

依輸入類型分派：

- **直接文字**：跳到 Step 2
- **`.txt / .md`**：直接讀
- **圖片 / 其他格式**：由外部 pipeline（如 RapidOCR）先轉成文字再傳入

### Step 2 — 規則層候選抽取

呼叫 `scripts/extract.py extract --text "<原文>"`（或 `--file <path>`）。內部會：

1. 載入 5 張 CSV（`load_kb.py`）
2. 用 `aliases.csv` 做**最長匹配掃描**，產出候選 `[(span, alias, candidate_ids, position)]`
3. 同步把 `landmark_index.csv` 的景點/機場也納入候選

### Step 3 — 歧義消解

對每個有多重 `candidate_ids` 的候選（或 `aliases.csv` 中 `alias_type='ambig'`），依序：

1. 找出該詞在 `disambig_rules.csv` 中的所有規則
2. 在文本中取**上下文視窗**（±N 字元，N 由 `window` 欄位指定，或 `full` 整篇）
3. 用 `context_regex` 比對視窗
4. 命中規則中 priority 最高者勝出，`resolution` = `SKIP` 則丟棄，否則綁定 `location_id`
5. 若無規則命中且仍多重，**標記 `needs_review`**，留給 Step 6 LLM 判斷

### Step 4 — 角色判定

對每個地名候選，掃 `role_keywords.csv`：

- 在地名前後視窗中找關鍵詞
- 命中 `departure / transit / exclude / metaphor / return` 任一 → **標記後不出現在最終標籤**
- 命中 `destination` → 強化信心度
- 無命中 → 預設 `destination`

特殊規則：機場類 landmark（type=airport）**預設 transit**，除非有 `destination` 關鍵詞或上下文無轉機/集合詞。

### Step 5 — 景點反向映射

`landmark_index.csv` 中匹配到的景點（如「富士山」「101」「合掌村」），自動補出 `location_id` → 加入結果樹的 city 層；同時繼承到 region/country 層。

### Step 5.5 — 弱信號掃描（v0.2 新增）

掃描原文中是否出現 `weak_signals.csv` 列出的跨國行銷詞（共 ~180 筆）：

- **沒命中** → 跳過
- **命中且該詞 `possible_countries` 中有國家已被識別為強信號** → 跳過（強信號錨定後，弱信號不需警告）
- **命中但 `possible_countries` 中沒有國家被識別** → 加入 `weak_warnings`，提示可能國家清單與必要的強信號條件

範例：
```
迪士尼度假村 5 日遊  → 強信號為空 → 警告「迪士尼可能是中國/日本/法國/美國/香港」
東京迪士尼 5 日遊    → 東京命中 → 無警告
越南富國島 Charm of Venice → 越南命中 → 不警告「威尼斯」是義大利
```

### Step 6 — LLM 校驗（subagent 或直接由 Claude 處理）

把 Step 2-5 的中間結果連同原文交給自己（Claude）做最後判讀，prompt 模板在 `prompts/llm_disambig.md`。

LLM 任務：

1. **確認**規則層判斷是否合理 → 給每個地名 0.0-1.0 信心度
2. **補抓**規則沒覆蓋的隱含地名（如沒在表裡的新景點），輸出 `unknown_terms` 清單給人工複核
3. **裁決** `needs_review` 的歧義
4. **檢查**比喻/否定上下文是否被正確排除

LLM 輸出 JSON：
```json
{
  "confirmed": [
    {"location_id": "JP-HKD-FRN", "name": "富良野", "confidence": 0.98, "evidence": "富良野薰衣草"}
  ],
  "rejected": [
    {"text": "桃園", "reason": "機場集合 transit"}
  ],
  "needs_review": [
    {"text": "合掌村", "candidates": ["JP-CBU-SKR"], "reason": "未在表中但 LLM 推斷"}
  ]
}
```

### Step 7 — 層級壓縮 + 輸出

把確認的地名按 `country → region → city → spot` 建樹，去重，輸出：

- **`result.json`**：完整結構 + 證據句 + 信心度
- **`result.md`**：人類可讀標籤摘要 + 待人工確認清單

最終標籤格式（範例）：
```
國家：日本
地區：北海道
城市：富良野
景點：富田農場（薰衣草）
信心度：0.97
證據：「富良野薰衣草 5 日」
```

## 維護對應表（增刪改）

### 路徑 A：用 Excel 編輯（推薦給業務人員）

直接打開 `C:\Users\User\Desktop\2d3d\旅遊表\旅遊DM國家關鍵字排除Mapping表.xlsx`：

- Sheet 1「國家Mapping表」：每國一列，編輯強關鍵字、要排除/降權關鍵字、需人工覆核條件
- Sheet 2「弱信號排除詞庫」：跨國行銷詞（迪士尼、極光、馬爾地夫級…）

編完存檔後：

```bash
python scripts/import_xlsx.py
```

會增量寫入 6 張 CSV（locations/aliases/landmark/weak_signals），保留所有原有人工調校的規則（disambig_rules/role_keywords）。

> **重要**：Excel = 業務維護權威主檔。CSV 是運行時快取。
> `update_kb.py` CLI 主要供開發 hotfix；**業務日常請走 Excel**，否則編輯 Excel 時看不到 CLI 新增的列，可能誤刪。

### 路徑 B：用命令列 `update_kb.py`（適合精準操作或新增 CSV 才有的欄位）

```bash
# 新增城市
python scripts/update_kb.py add-location --id JP-HKD-WKK --name 稚內 --parent JP-HKD --type city

```bash
# 新增城市
python scripts/update_kb.py add-location --id JP-HKD-WKK --name 稚內 --parent JP-HKD --type city

# 新增別名
python scripts/update_kb.py add-alias --alias 東瀛之都 --location JP-KTO-TYO --type nickname

# 新增歧義規則
python scripts/update_kb.py add-rule --term 神戶 --context "日本|港口" --resolution JP-KSI-KOB --priority 90

# 新增景點
python scripts/update_kb.py add-landmark --name 哲學之道 --city JP-KSI-KYT --type district

# 列出/查詢
python scripts/update_kb.py list --type country
python scripts/update_kb.py find --term 高山
```

每次寫入：

1. 預檢重複（同 id、同 alias→同 id 視為重複）
2. 寫入後印一份 git diff 風格的摘要供 review
3. 若有 LLM Step 6 提的 `unknown_terms`，提示用戶可批次新增

## 業務範圍（v0.2 — 整合 Excel 主檔後）

41 個國家/地區，涵蓋大都會旅遊現有與潛在拓展市場：

| 大區 | 國家 |
|---|---|
| 東亞 | 台灣、日本、韓國、中國 |
| 港澳 | 香港、澳門 |
| 東南亞 | 泰國、越南、新加坡、馬來西亞、印尼、菲律賓、柬埔寨 |
| 中東 | 土耳其、阿聯酋/杜拜 |
| 歐洲 | 義大利、法國、瑞士、奧地利、德國、英國、荷蘭、西班牙、葡萄牙、希臘、捷克、匈牙利、克羅埃西亞、冰島、芬蘭、挪威 |
| 非洲 | 埃及、摩洛哥、南非 |
| 大洋洲 | 澳洲、紐西蘭 |
| 美洲 | 美國、加拿大、墨西哥 |
| 南亞 | 印度、尼泊爾 |

v0.1 的 DDV 9 大分類仍是**地區/城市層**最完整的部分（含 region 與 city）。
v0.2 新增 33 國目前以**國家層**為主，城市/景點掛在國家 root 下，未來再細化。

## 九個必過案例（驗證錨點）

驗證在 `tests/fixtures/` 下：

| 案例 | 輸入 | 期望結果 |
|---|---|---|
| A | 日本飛驒高山合掌村 5 日，含富士山賞櫻 | JP / 中部 / 高山 + 白川鄉 + JP / 中部 / 山梨 |
| B | 台灣桃園機場集合，搭機飛日本東京 | JP / 關東 / 東京（桃園被角色濾掉）|
| C | 江南東方威尼斯水鄉 6 日 | CN / 華東（不誤標義大利威尼斯）|
| D | 101 跨年夜 | TW / 台北（景點反向映射）|
| E | 曼谷轉機飛峇里島 | ID / 峇里島（曼谷被 transit 濾掉）|
| F | 搭乘高山火車欣賞山脈美景 | 無標籤（「高山」是一般詞）|
| G | 迪士尼度假村 5 日遊 | 無標籤 + 弱信號警告「迪士尼可能是多國」|
| H | 東京迪士尼 5 日遊 | JP / 關東 / 東京（不發弱信號警告）|
| I | 越南富國島 Grand World 仿威尼斯水都遊船 | VN / 富國島（不誤標義大利）|

跑：
```bash
python scripts/extract.py test
```

## 與工作目錄的同步

主表存放於 `~/.claude/skills/travel-geo-extract/data/`（規範版）。

`C:\Users\User\Desktop\2d3d\旅遊表\data\` 放一份**人工編輯副本**，方便用 Excel 維護。`update_kb.py` 預設寫到 skill 目錄；如需從工作目錄 sync，用：

```bash
python scripts/update_kb.py sync --from "C:/Users/User/Desktop/2d3d/旅遊表/data"
```

## 已知限制（v0.1）

- 圖片 OCR 對中文 DM 仍有 5-10% 字錯率，建議重要文案先人工校對
- 景點層只覆蓋約 100+ 熱門景點，新景點需手動加表（LLM 會提示）
- 港澳台目前各自獨立 root，未做大中華圈聚合
- 規則表用 priority 解平手；極端歧義（如「中國」=日本 vs 中華人民共和國）信賴上下文 30 字內出現國別線索

## 參考檔案

- `reference/ddv_taxonomy.md` — 大都會網站分類對照
- `prompts/llm_disambig.md` — LLM 消歧 prompt 模板
- `tests/fixtures/` — 案例 A-E 完整輸入與期望輸出
