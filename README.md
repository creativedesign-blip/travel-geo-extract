# 大都會旅遊 — 國家/地區對應表

## 主檔

> **流程鐵則**：業務改 Excel，工程跑 import。**CLI add-* 只供緊急 hotfix**，加完務必貼回 Excel。

**業務人員維護**：`旅遊DM國家關鍵字排除Mapping表.xlsx`

- Sheet 1「國家Mapping表」：每國一列，41 國，含強關鍵字、要排除/降權關鍵字、需人工覆核條件、狀態、維護日期
- Sheet 2「弱信號排除詞庫」：跨國行銷詞詞庫（迪士尼、極光、馬爾地夫級等 18 條）
- Sheet 3「使用說明」+ Sheet 4「維護設定」：業務規範

**編完 Excel 後請跑**：
```bash
python "C:/Users/User/.claude/skills/travel-geo-extract/scripts/import_xlsx.py"
```
會自動把 Excel 內容增量寫入 6 張 CSV，並重新生成 `weak_signals.csv`。

## CSV（運行時快取版本）

| 檔 | 內容 |
|---|---|
| `data/locations.csv` | 國家／地區／城市階層表 |
| `data/aliases.csv` | 別名（英文/日文/舊名/俗稱）→ location_id |
| `data/disambig_rules.csv` | 同名歧義消解規則（高山火車 vs 日本高山）|
| `data/landmark_index.csv` | 景點／機場 → 所屬城市 |
| `data/role_keywords.csv` | 角色關鍵詞（集合／轉機／比喻，含 direction 欄）|
| `data/weak_signals.csv` | 跨國弱信號詞庫（v0.2 新增，由 Excel 匯入）|

## 編輯後同步回 skill

```bash
# 把這份副本同步到 skill 主目錄（生效）
python "C:/Users/User/.claude/skills/travel-geo-extract/scripts/update_kb.py" \
  sync --from "C:/Users/User/Desktop/2d3d/旅遊表/data"
```

## 從 skill 拉最新版本到這裡

```bash
python "C:/Users/User/.claude/skills/travel-geo-extract/scripts/update_kb.py" \
  sync --to "C:/Users/User/Desktop/2d3d/旅遊表/data"
```

## CSV 編輯注意事項

1. **編碼**：UTF-8 with BOM（Excel 預設）即可正常打開中文
2. **欄位含逗號**：用雙引號包起來（例如 regex `經[^,。]*飛` 要寫成 `"經[^,。]*飛"`）
3. **id 命名規則**：
   - 國家：2 碼，如 `TW`、`JP`
   - 地區：`<國家碼>-<3碼>`，如 `JP-KTO`（關東）、`CN-EAS`（華東）
   - 城市：`<地區碼>-<3碼>` 或 `<國家碼>-<3碼>`（無地區層）
4. **不要刪行**：要停用某個地點，把 `active` 改為 `0`（軟刪除），保留歷史對應
5. **新增別名前**：先用 `python update_kb.py find --term <關鍵字>` 確認沒重複

## 在 Claude Code 用 skill

對話中直接說：

- 「幫我抽這份 DM 的國家地區」
- 「打標籤這份 PDF：C:/path/to/dm.pdf」
- 「新增『稚內』到日本北海道」
- 「『神戶』在港口情境下要映射到 JP-KSI-KOB」

Claude 會自動呼叫 `travel-geo-extract` skill。

## 命令列直接跑

```bash
# 文字
python "C:/Users/User/.claude/skills/travel-geo-extract/scripts/extract.py" \
  extract --text "日本飛驒高山合掌村 5 日" --format md

# 檔案
python "C:/Users/User/.claude/skills/travel-geo-extract/scripts/extract.py" \
  extract --file "C:/path/to/dm.txt" --format md

# 回歸測試
python "C:/Users/User/.claude/skills/travel-geo-extract/scripts/extract.py" test
```

## v0.1 業務範圍

- **國內**：台灣 19 縣市
- **東南亞**：泰、越、菲、印
- **中國**：華東、西北、華北、東北（4 大區）
- **日本**：關東、關西、東北、北海道、九州 + 中部、中國、四國、沖繩
- **韓國**：6 大城

港澳、歐美、非洲不在 v0.1 範圍，會 SKIP 並列在 `unknown_terms` 提示。
# travel-geo-extract
