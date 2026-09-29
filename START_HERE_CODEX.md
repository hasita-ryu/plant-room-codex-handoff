# PLANT ROOM — Codex引き継ぎ

このフォルダは、植物管理Webアプリ「PLANT ROOM」の現時点の実装・生成済み画像素材・仕様をまとめた引き継ぎパッケージです。

部屋画面の見た目・配置・キャラクター挙動の細かい基準は `ROOM_VISUAL_SPEC.md` を優先して確認すること。  
部屋や植物配置が現実的に成立しているかは `REALISM_CHECKLIST.md` で確認すること。
両者の整合性チェック結果は `SPEC_REALISM_AUDIT.md` に残している。

## まずやること

1. `app/` を現行アプリ本体として扱う。
2. `assets/` 内の新しいドット絵を確認する。
3. 下記の「固定仕様」を壊さず、まず生成済み素材をアプリへ統合する。
4. ローカルでPC表示とスマホ横表示を確認する。
5. 変更後は植物登録・配置・水やり・枯れ処理の回帰確認を行う。

API連携は今は後回し。OpenAI APIキーの設定やAI生成機能の本番化は、ユーザーから改めて指示があるまで優先しない。

---

## アプリの目的

植物を「種類単位」で管理しながら、所有している植物をレトロなドット絵の部屋へ配置できる個人用アプリ。

単なる植物台帳ではなく、現実の植物管理とゲームの部屋づくりを連動させる。

公開中URL:
https://plant-room-web-production.up.railway.app

現行構成:
- Backend: FastAPI
- DB: SQLite
- Frontend: Vanilla HTML / CSS / JavaScript
- Hosting: Railway
- Railway persistent volume: `/data`
- 植物登録: PCのみ
- スマホ: 横画面前提

---

# 固定仕様 — 勝手に変えないこと

## 植物データ

植物は個体単位ではなく**種類単位**で管理する。

例:
- シェフレラ ×3 = 1レコード、count=3
- 水やり状態も種類単位
- その種類を「水やりした」にすると、その種類全体が水やり済みになる
- 個体差は扱わない

枯れた植物:
- alive countを1減らす
- dead countを1増やす
- 枯れた植物は部屋へ残す/配置することができる

捨てた・使用しただけの場合:
- countを減らす
- dead historyには追加しない

誤登録削除:
- UI上から消す
- DB上は `deleted_at` によるsoft delete

## 水やり

ユーザーはアプリが通知したタイミングで水やりする想定。

- 種類ごとに次回水やり日を持つ/算出する
- `水やりした`
- `全部水やりした`
- 枯れ状態は自動判定しない。手動のみ

植物の見た目状態:
- normal / healthy
- thirsty / needs water
- dead

水やりすると thirsty → normal。

## 湿度表示

数値センサーではなく簡単な3分類程度。

- 低湿度でも管理しやすい
- 一般的な室内湿度で管理
- 高湿度を好む

## 部屋

アプリ起動時のメイン画面はダッシュボードではなく**部屋**。

- 正面視点の2Dドット絵
- アイソメトリック禁止
- 16×10の配置グリッドが現行基準
- 正面壁にはドアを置かない
- 窓は正面壁の左寄り

### ドア仕様（重要）

ドアは**側面の壁**にある。

通常時:
- 正面からドアは見えない
- 側面壁だけが少し見える程度
- ドアを背景の常設要素として表示しない

主人公の入室時だけ:
- 側面のドアが開く
- 画面端からドアの縁/暗い開口部が一瞬だけチラッと見える
- 主人公がそこから入室する
- 入室後は閉じて、ドアは再び見えなくなる

**生成済みroom sheetより、この仕様を優先すること。生成素材の向きや見え方が違う場合は素材側を調整する。**

## 家具

基本家具:
- bed ×1
- desk ×1
- chair ×1
- shelf ×1
- small shelf ×1
- side table ×1

現在は各家具1色でよい。色違いは不要。

追加素材として以下も生成済み:
- plant stand
- wall shelf
- dresser
- floor lamp
- rug
- wall picture

家具は配置モードで移動できる。

## 植物の配置

配置タイプ:
- floor / shelf
- wall-mounted
- hanging

吊り下げは高さ4段階。

同じ種類を部屋へ置ける数は、所有数を超えてはいけない。

横反転機能は不要。

## 将来の部屋/エリア

現時点では自室がMVP。

将来:
1. 自室
2. 植物棚エリア
3. ベランダ
4. 温室
5. 植物専用室

ざっくり累計植物数5/10/20/30でアンロックする想定。一度開いたら再ロックしない。

## 主人公

主人公は直接操作キャラクターではなく、部屋で生活しているアンビエントキャラクター。

- 入室
- 歩く
- 座る
- 寝る
- 植物を見る
- 水やり
- 植物を持つ

などの素材を生成済み。

将来的には家具/植物との衝突判定が必要。

---

# ドット絵の共通ルール

ユーザーが指定した共通スタイル:

`true pixel art, hard-edged square pixels, no anti-aliasing, no smoothing, nearest-neighbor style, limited color palette, 1px pixel outline`

参考の方向性:
- ドット1個1個がはっきり見える
- 細かすぎない
- 可愛いデフォルメ
- 4〜8色程度を中心にした限定パレット
- 1px程度の輪郭
- 滑らかなグラデーションなし
- 左上光源を基本
- 家具より植物を主役にする
- 背景はオブジェクトより情報量を落とす

### 画像生成の運用ルール

差分を1枚ずつ別々に生成しない。

**同じジャンル/同じ植物/同じ部屋の差分は1枚のsprite sheetとしてまとめて生成する。**

目的は、差分間で絵柄・サイズ・色・密度が変わるのを防ぐこと。

例:
- ヒポエステスの9状態 → 1枚
- 同じ部屋のnormal / door closed / door open → 1枚
- 同じ家具セット → 1枚

---

# 生成済み素材

`assets/plant_asset_bundle/`

第一弾:
- `cut/rooms/` — 自室3状態
- `cut/furniture/` — bed, desk, chair, shelf, small_shelf, side_table
- `cut/character/` — standing, walk1/2, sitting, sleeping, looking at plant, entering1/2
- `cut/plants/` — ヒポエステス9差分
- `cut/ui/` — 基本UIアイコン
- `sheets/` — 切り出す前の元sprite sheets

`assets/plant_asset_bundle_part2/`

第二弾:
- `cut/character_extra/` — 水やり、家具利用、植物を持つ等
- `cut/ui_extra/` — 追加UI
- `cut/furniture_extra/` — 植物台、壁棚、タンス、ランプ、ラグ、額縁
- `cut/plants_extra/` — Monstera / Ivy / Pothosの状態差分
- `cut/effects/` — 水滴、配置可否、選択枠、吹き出し等
- `sheets/` — 切り出す前の元sprite sheets

注意:
- `sheets/` は一貫性確認用の元画像。
- 実装には原則 `cut/` の個別PNGを使う。
- AI生成なので、切り出し境界や余白は必要に応じて微調整してよい。
- 元素材を勝手に削除せず、必要なら `app/public/assets/` にコピーして使う。

---

# Codexで次に進める推奨順

## 1. 新画像のアプリ統合

現行 `app/public/assets/` には古い簡易素材が残っている。

まず以下を新素材へ置き換える/追加する:
- 基本家具6種
- 主人公
- ヒポエステス
- UIアイコン
- エフェクト

元の生成画像は保持する。

## 2. サイズ調整

画像を入れてから、実画面を見ながら家具サイズを決める。

「先にCSS上の家具サイズを決め打ちしてから画像を合わせる」のではなく、生成済み素材の見た目を基準に自然な相対サイズを詰める。

## 3. 主人公アニメーション更新

- 入室時にだけ側面ドアを見せる
- walk 1/2をループ
- chair / desk / bedの利用差分を使う
- furnitureを動かした場合、座る位置/寝る位置も家具位置に追従できる構造へ寄せる

## 4. 配置UIの改善

生成済みの:
- valid placement
- invalid placement
- selection frame
- movement arrow

を使って、配置可否が視覚的に分かるようにする。

## 5. 回帰テスト

最低限:
- 新規アカウント
- 植物種類登録
- ×3管理
- 部屋に配置
- 所有数以上配置できない
- 水やり対象化
- 水やり完了
- 枯れ処理 count 3→2 / dead 1
- 枯れた個体を配置可能
- PC
- スマホ横
- サーバ再起動後もデータが残る

---

# AI/APIについて

AI機能のコード自体はあるが、現在の優先度は低い。

ユーザーは「まだAPIはいい」と明言しているので、今はUI/画像/ゲーム部分を優先する。

APIキーをコードやリポジトリへ直書きしないこと。

---

# 起動

`app/` で:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8787
```

ブラウザ:

`http://localhost:8787`

Railway用のDockerfile/railway.jsonも `app/` に含まれている。

---

# 最重要

実装上の判断に迷った場合の優先順位:

1. この引き継ぎ文書の固定仕様
2. ユーザーが次にCodexへ出す最新指示
3. 既存コード
4. 生成画像の見た目

生成画像に仕様との矛盾があれば、**仕様を優先して画像/表示を調整すること。**
