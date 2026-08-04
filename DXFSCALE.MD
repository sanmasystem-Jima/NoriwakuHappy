# DXFSCALE.MD — 法枠Happy 縮尺管理仕様 v01

作成日: 2026-08-03
対象: 法枠Happy(Z:\法枠Happy)
実装担当: Claude Code
配置場所: リポジトリルート

---

## 0. この文書の位置づけ

本書は法枠HappyにおけるDXF出力の**縮尺の扱いを規定する唯一の正**である。
縮尺に関する判断は本書に従うこと。本書と実装が食い違う場合、実装を直す。

既存のHappyシリーズ(桝Happy / BrockHappy)にも将来的に適用する前提で書くが、
本仕様の適用範囲はまず法枠Happyに限定する。

---

## 1. 背景 — なぜ規定が必要か

### 1.1 DXFには縮尺の概念が存在しない

| | 縮尺の持ち方 |
|---|---|
| V-nas Clair / Jw_cad | レイヤ(グループ)ごとに縮尺を保持。図形は実寸、文字・寸法は**用紙上mm**で指定しCADが自動換算 |
| DXF | モデル空間は実寸のみ。**縮尺を記録する欄が規格上ない**。文字高さも実寸値で書くしかない |
| SXF (P21) | レイヤごとに縮尺欄を持つ |

DXFのレイヤテーブル(TABLES/LAYER)が持てるのは色・線種・線幅・表示状態のみ。
**DXFのレイヤから縮尺を読むことはできない。**

このため V-nas はDXF読込時に縮尺をユーザーに問い合わせる。これは仕様であり回避できない。

### 1.2 過去に発生した実害

- BrockHappy: `$INSUNITS` 未設定により、実寸4.5mが V-nas 上で5.4mとして読まれた
- BrockHappy: 寸法矢印の内外反転がCAD側レンダリング判断で発生。閾値が縮尺依存のため、
  縮尺変更のたびに手動修正が無効化された

### 1.3 AutoCAD注釈尺度(annotative)について

DXFには注釈尺度の仕組みがあるが、**V-nas Clair が正しく解釈する保証がない**。
本仕様では使用を禁止する。

---

## 2. 基本原則

以下5点を絶対原則とする。

1. **モデル空間は常に実寸mm**(m値 × 1000)。座標を縮尺で割ることを禁止する
2. **縮尺はツール側のレイヤ定義表が唯一の正**とし、DXFから読み取ろうとしない
3. **文字・寸法サイズは「用紙上mm × レイヤ縮尺」で算出**する。コードに実寸値のリテラルを書かない
4. **1ファイル1縮尺**。縮尺が異なる図はDXFファイルを分割する
5. **ラス図・割付図は1/100固定**。他縮尺の指定は例外で拒否する

---

## 3. レイヤ定義表 `layers.json`

### 3.1 配置

```
Z:\法枠Happy\config\layers.json
```

### 3.2 スキーマ

```json
{
  "version": "01",
  "unit": "mm",
  "layers": {
    "<レイヤ名>": {
      "scale":          <int>,     必須 縮尺分母 (1/100 なら 100)
      "color":          <int>,     必須 AutoCAD Color Index
      "ltype":          "<str>",   必須 線種名
      "lineweight":     <int>,     任意 0.01mm単位 / 省略時 -3 (既定)
      "text_paper_mm":  <float>,   任意 文字を持つレイヤのみ
      "arrow_paper_mm": <float>,   任意 寸法レイヤのみ
      "group":          "<str>"    必須 出力ファイル分割キー (§6)
    }
  }
}
```

### 3.3 初期定義(暫定・実装時に現物合わせで調整可)

```json
{
  "version": "01",
  "unit": "mm",
  "layers": {
    "H-RAS":       { "scale": 100, "color": 7, "ltype": "CONTINUOUS", "group": "ras" },
    "H-RAS-DIM":   { "scale": 100, "color": 3, "ltype": "CONTINUOUS", "group": "ras",
                     "text_paper_mm": 2.5, "arrow_paper_mm": 2.0 },
    "H-RAS-TXT":   { "scale": 100, "color": 2, "ltype": "CONTINUOUS", "group": "ras",
                     "text_paper_mm": 3.0 },
    "H-WRK":       { "scale": 100, "color": 7, "ltype": "CONTINUOUS", "group": "wariduke" },
    "H-WRK-GRID":  { "scale": 100, "color": 8, "ltype": "DASHED",     "group": "wariduke" },
    "H-WRK-NUM":   { "scale": 100, "color": 2, "ltype": "CONTINUOUS", "group": "wariduke",
                     "text_paper_mm": 2.0 },
    "H-DTL":       { "scale": 20,  "color": 7, "ltype": "CONTINUOUS", "group": "dtl" },
    "H-DTL-DIM":   { "scale": 20,  "color": 3, "ltype": "CONTINUOUS", "group": "dtl",
                     "text_paper_mm": 2.5, "arrow_paper_mm": 2.0 },
    "H-DTL-TXT":   { "scale": 20,  "color": 2, "ltype": "CONTINUOUS", "group": "dtl",
                     "text_paper_mm": 3.0 },
    "_SCALE_CHECK":{ "scale": 100, "color": 1, "ltype": "CONTINUOUS", "group": "*" }
  }
}
```

`_SCALE_CHECK` の `group` は `"*"` = 全ファイルに出力(§7.4)。

### 3.4 制約

- 同一 `group` 内のレイヤは **`scale` が全て一致していなければならない**。
  不一致は起動時バリデーションで `LayerScaleConflictError` を送出して停止する
- レイヤ名はコード中に文字列リテラルで散らさず、定数モジュールに集約する

---

## 4. 縮尺解決ロジック

### 4.1 固定縮尺の強制

ラス図・割付図は1/100固定とする。デフォルト値ではなく**拒否**で実装する。

```python
FIXED_SCALE = {
    "ras":      100,   # ラス図
    "wariduke": 100,   # 割付図
}
DEFAULT_SCALE = 100

def resolve_scale(group: str, requested: int | None = None) -> int:
    if group in FIXED_SCALE:
        fixed = FIXED_SCALE[group]
        if requested is not None and requested != fixed:
            raise ScaleNotAllowedError(
                f"{group} は 1/{fixed} 固定です(指定値 1/{requested} は受け付けません)"
            )
        return fixed
    return requested if requested is not None else DEFAULT_SCALE
```

### 4.2 GUI側の挙動

- 図種で「ラス図」「割付図」が選択された場合、縮尺入力欄を**disabled化**し
  `1/100 固定` と表示する
- 入力させてから例外で弾くUIにしない。触れないようにする
- 断面詳細(`dtl`)は可変のまま。選択肢は 1/10, 1/20, 1/30, 1/50 とする

### 4.3 例外クラス

```python
class ScaleError(Exception): ...
class ScaleNotAllowedError(ScaleError): ...
class LayerScaleConflictError(ScaleError): ...
```

---

## 5. サイズ算出

### 5.1 唯一のアクセサ経由とする

文字高さ・矢印サイズは以下の関数からのみ取得する。
描画コード内で `scale` を直接掛ける記述を禁止する。

```python
def text_height(layer: str) -> float:
    """実寸mmでの文字高さを返す"""
    L = LAYERS[layer]
    if "text_paper_mm" not in L:
        raise KeyError(f"{layer} は文字用レイヤではありません")
    return L["text_paper_mm"] * L["scale"]

def arrow_size(layer: str) -> float:
    L = LAYERS[layer]
    return L["arrow_paper_mm"] * L["scale"]

def scale_of(layer: str) -> int:
    return LAYERS[layer]["scale"]
```

例: `H-RAS-TXT` → 3.0 × 100 = **300.0** (実寸mm)

### 5.2 テキストスタイル

- テキストスタイルの `height` は **0** に設定し、エンティティ側の `height` を有効にする
- 固定高スタイルを作らない

```python
style = doc.styles.add("HAPPY", font="msgothic.ttc")
style.dxf.height = 0
```

### 5.3 寸法スタイル

寸法スタイルは**縮尺ごとに1つ**作成する。命名は `HAPPY_S<分母3桁>`。

```python
ds = doc.dimstyles.add(f"HAPPY_S{scale:03d}")
ds.dxf.dimscale = scale     # 寸法要素の一括倍率
ds.dxf.dimtxt   = 2.5       # 用紙上mm (dimscale が掛かる)
ds.dxf.dimasz   = 2.0       # 用紙上mm (dimscale が掛かる)
ds.dxf.dimexe   = 1.25
ds.dxf.dimexo   = 1.0
ds.dxf.dimgap   = 0.625
ds.dxf.dimdec   = 0
ds.dxf.dimlunit = 2
```

`dimscale` を使う場合、`dimtxt` / `dimasz` は**用紙上mmをそのまま書く**。
§5.1 の `text_height()` と二重に掛けないこと。**寸法エンティティは dimstyle 経由、
単独テキストは text_height() 経由**と役割を明確に分ける。

### 5.4 矢印内外反転の閾値

BrockHappy で発生した問題への対処を引き継ぐ。縮尺が固定になったことで定数化できる。

```python
def needs_flip_fix(dim_length_mm: float, layer: str, text_str: str) -> bool:
    s = scale_of(layer)
    drawn = dim_length_mm / s                      # 用紙上mm
    arrow = LAYERS[layer]["arrow_paper_mm"] * 2
    text  = LAYERS[layer]["text_paper_mm"] * len(text_str) * 0.6
    return drawn < (arrow + text)
```

該当した寸法は `dimtix = 1` を個別付与する。

---

## 6. ファイル分割と命名

### 6.1 分割規則

`layers.json` の `group` 単位でDXFファイルを分割する。
1ファイル1縮尺を厳守する(§2-4)。

| group | 内容 | 縮尺 |
|---|---|---|
| `ras` | ラス図 | 1/100 固定 |
| `wariduke` | 割付図 | 1/100 固定 |
| `dtl` | 断面詳細 | 可変 |

### 6.2 命名規則

```
houwaku_<group>_S<分母3桁>.dxf
```

例:
```
houwaku_ras_S100.dxf
houwaku_wariduke_S100.dxf
houwaku_dtl_S020.dxf
```

**縮尺をファイル名に必ず含める。** V-nas読込ダイアログで縮尺を選ぶ際、
人間が正解を目視できることが誤読込防止に最も効く。

### 6.3 出力先

```
Z:\法枠Happy\output\<案件名>\
```

---

## 7. 縮尺の痕跡埋め込み

DXFに縮尺欄はないが、以下4種の痕跡を残す。すべて実装する。

### 7.1 DXFヘッダ変数

全出力ファイルに設定する。**`$INSUNITS` の設定漏れは過去に実害が出ている。**

```python
doc.header['$INSUNITS']    = 4      # 4 = mm
doc.header['$MEASUREMENT'] = 1      # 1 = メートル法
doc.header['$LTSCALE']     = scale  # 破線・鎖線ピッチ
doc.header['$CELTSCALE']   = 1
doc.header['$PSLTSCALE']   = 0
doc.header['$DIMSCALE']    = scale
```

### 7.2 カスタム変数

V-nas は読まないが、自作ツールが再読込する際の判定に使う。

```python
doc.header.custom_vars.append("SCALE_DENOM", str(scale))
doc.header.custom_vars.append("DRAWING_GROUP", group)
doc.header.custom_vars.append("GEN_TOOL", f"houwakuHappy {VERSION}")
doc.header.custom_vars.append("GEN_DATE", datetime.now().isoformat())
```

### 7.3 図面内注記

図枠内に `S=1:100` を描画する。図面として本来必要なものを兼ねる。
レイヤは `H-RAS-TXT` 等の文字レイヤを使う。

### 7.4 縮尺確認線

レイヤ `_SCALE_CHECK` に、原点から X方向 **実寸1000mm** の直線を1本引く。

- 全出力ファイルに入れる
- 読込後にこの線を計測し 1.000m であれば単位が正しく通っている
- **納品前に削除する**。削除忘れ防止のため、出力時にコンソールへ警告を出す

```
[WARN] _SCALE_CHECK レイヤを含んでいます。納品前に削除してください。
```

---

## 8. 割付図への波及(参考)

縮尺が1/100固定になることで、割付処理の設計時に以下が確定できる。
毎回の動的判定が不要になる。

- 標準枠間隔 1.5m → 図面上 15mm / 2.0m → 図面上 20mm
- 枠番号文字(`H-WRK-NUM`, 用紙上2.0mm)が枠内に収まるかは設計時に一度決めれば足りる
- 収まらない場合の間引き表示ルールも定数化できる

具体的な割付アルゴリズムは本書の範囲外。別途仕様書とする。

---

## 9. 禁止事項

以下を実装してはならない。

- 座標を縮尺で除して用紙原寸で出力すること(実寸が壊れ、数量計算・電子納品が通らない)
- ペーパー空間 + ビューポートによる縮尺表現(V-nasのレイアウト読込が不安定)
- AutoCAD注釈尺度(annotative)の使用
- 1つのDXFファイルに異なる縮尺のレイヤを混在させること
- 描画コード内での文字高さ実寸値のハードコード
- `text_height()` を経由しないサイズ算出

---

## 10. 受入条件

実装完了の判定基準。全項目を満たすこと。

1. `layers.json` を読み込み、`group` 内の `scale` 不一致を起動時に検出して停止する
2. `resolve_scale("ras", 50)` が `ScaleNotAllowedError` を送出する
3. GUIでラス図・割付図選択時、縮尺欄が disabled になり `1/100 固定` と表示される
4. 出力DXFの `$INSUNITS` が 4 である
5. 出力ファイル名に縮尺分母が3桁で含まれている
6. 異なる縮尺のレイヤが同一ファイルに出力されない
7. V-nas Clair で 1/100 指定して読み込んだとき
   - `_SCALE_CHECK` の線が 1.000m と計測される
   - `H-RAS-TXT` の文字が用紙上 3.0mm 相当で表示される
   - 破線が実線に見えない(`$LTSCALE` が効いている)
8. 描画モジュール内を `grep` して、文字高さの実寸リテラル(例: `300`)が出現しない

---

## 11. 改訂履歴

| ver | 日付 | 内容 |
|---|---|---|
| 01 | 2026-08-03 | 初版 |
