# 777CON-PASS 自動申込システム（Intel Mac + iPhone 実機 / Appium + XCUITest）

指定日時に、指定店舗・指定来店日の申込を iPhone 実機上の 777CON-PASS アプリで実行する仕組みです。
Python、Appium、XCUITest（WebDriverAgent）、launchd、FastAPI を使っています。

> **現状（重要）**
> - コード、テスト、セットアップスクリプト、管理画面は完成しています。シミュレートしたアプリで失敗系を含めて **23件のテストがすべて通っています**。
> - **アプリの画面構造はまだ調査していません。** `config/selectors.yaml` の値はすべて `TODO`（未検証）です。この状態では **本番モードは起動自体を拒否** します（`not_ready`）。
> - フェーズ1〜3の実機作業（環境診断、WDA署名、画面調査）は、あなたの Mac と iPhone で行う必要があります（手順は後述）。

---

## 0. 利用規約について（最初に確認してください）

- 777CON-PASS（サミーネットワークス運営）の利用規約に、**自動操作ツールの使用を禁止する条項があるか**を、アプリ内の「利用規約」で必ず確認してください。私はWeb上で規約本文を見つけられず、確認できていません。
- 2026年10月には、同サービスへの大量の自動リクエストによる不正アクセス事案が公表されています。運営は自動アクセスを監視していると考えるべきです。
- 禁止されている場合は使わないでください。アカウント停止のリスクがあります。
- このシステムは次の設計にしています。
  - 本人のアカウントで、本人の iPhone の公式アプリ画面を、人と同じ手順でタップするだけです。
  - APIを直接呼んだり、通信を改ざんしたりはしません。
  - CAPTCHAや追加認証は自動で回避しません。検出したら停止し、手動での対応を依頼します。
  - 1ジョブにつき申込タップは原則1回です。ポーリングや連打はしません。リトライの間隔は2〜10秒の指数バックオフで、申込前の画面遷移だけが対象です。

## 1. ディレクトリ構成

```
conpass/
  config.py      予約設定(YAML)の読込・検証（来店日・受付期間・重複）
  selectors.py   画面/手順の宣言的定義ローダ、未検証セレクタ判定
  device.py      Appium XCUITest セッション（noReset=true でログイン状態維持）
  engine.py      申込フロー本体（安全リトライ・二重申込防止・結果確認）
  store.py       SQLite：申込状態（二重申込ガード）・実行履歴・排他ロック
  timing.py      精密待機・時計オフセット測定（sntp）
  scheduler.py   launchd LaunchAgent 生成/登録/解除、pmset復帰コマンド生成
  runner.py      1ジョブ実行（ログ、caffeinate、遅延起動判定、通知、自動解除）
  recon.py       実機画面の調査（スクショ＋XML＋要素一覧）、セレクタ検証(probe)
  cli.py         コマンドライン
  web/           FastAPI 管理画面（127.0.0.1:8777 のみ）
config/
  selectors.yaml            画面マーカーと手順（★実機調査で埋める）
  reservations.example.yaml 予約設定の例
  device.example.yaml       接続設定の例（パスワード類は一切なし）
scripts/
  diagnose_mac.sh   環境診断（読み取りのみ）
  setup_mac.sh      不足分のインストール（バージョンは実行時に解決）
  start_appium.sh   Appium を localhost で起動
  find_bundle_id.sh アプリの Bundle ID 候補を表示
tests/              シミュレートしたアプリで検証（失敗系を含む）
data/               ログ・スクショ・DB（git管理外）
```

## 2. フェーズ1：環境構築（Mac で実行）

```bash
git clone <このリポジトリ> conpass-auto && cd conpass-auto
```

```bash
bash scripts/diagnose_mac.sh | tee diagnose.txt
```

診断結果は、私（Claude Code）に貼り付けてもらえれば判断できます。そのあと次を実行します。

```bash
bash scripts/setup_mac.sh
```

**互換性（2026-11-20 時点の調査）**

| 項目 | 内容 |
|---|---|
| Appium | 4.0.0（latest） |
| appium-xcuitest-driver | 13.4.0（latest）、peer `appium ^4.0.0` |
| Node.js | 両者とも `^22.22.2 \|\| ^24.15.0 \|\| >=26.0.0`、npm >= 10 |
| iOS 18 実機 | Xcode >= 16、macOS >= 14.3（XCUITest driver の system requirements による） |
| iOS 26 実機 | 対応する Xcode 26 系が必要です。Intel Mac で動く macOS / Xcode の上限を Apple の表で確認してください |
| Appium-Python-Client | 6.1.8 |

`setup_mac.sh` はバージョンを固定せず、実行時に `npm view appium engines.node` で要件を確認してから Node のバージョンを判定します。
iOS 18 以降では、driver が RemoteXPC トンネル（`sudo appium driver run xcuitest tunnel-creation --udid <UDID>`）を使える場合があります。通常の USB 接続で WDA が起動しない場合に試してください。

**あなたの操作が必要な箇所**

1. Xcode のインストール、`sudo xcodebuild -license accept`、`sudo xcodebuild -runFirstLaunch`（管理者パスワードが必要）
2. Xcode > Settings > Accounts に Apple ID を追加
3. `appium driver run xcuitest open-wda` → WebDriverAgentRunner の Signing で Team を選択し、Bundle ID を一意に変更
4. iPhone で「このコンピュータを信頼」、デベロッパモードON、「VPNとデバイス管理」で開発元を信頼
5. `config/device.yaml` に `udid` / `bundle_id` / `xcode_org_id` / `updated_wda_bundle_id` を記入

> 無料の Apple ID で署名した WDA は **7日で失効** します。本番実行の直前に、テスト実行で WDA が起動することを確認してください。

## 3. フェーズ2：iPhone 接続確認

```bash
bash scripts/start_appium.sh
```

上のコマンドは起動したままにします。以下は別のターミナルで実行します。

```bash
.venv/bin/python -m conpass.cli doctor
```

```bash
.venv/bin/python -m conpass.cli screenshot
```

`data/recon/` に PNG が保存されれば完了です。

## 4. フェーズ3：アプリ画面の調査（推測しないための手順）

```bash
.venv/bin/python -m conpass.cli recon
```

iPhone を手で操作して各画面を表示し、ラベル（`home`、`store_search`、`confirm` …）を入力して Enter を押します。
画面ごとに `data/recon/<日付>/` へ次の3つを保存します。

- `*.png` スクリーンショット
- `*.xml` XCUITest のページソース
- `*.json` 要素一覧（type / name / label / rect）

次のものは必ず取得してください。

- ホーム、ログイン画面（ログアウト状態で）、店舗検索、店舗詳細、日付選択、確認画面、完了画面、申込済み表示、受付終了表示、申込履歴（予約一覧）、通信エラー表示（機内モードで再現）

画面が WebView の場合（`contains WebView` と表示されます）は、要素が取れないことがあります。その場合は Appium Inspector で確認し、`by: image`（テンプレート画像）か `by: coords` を最後の手段として使います。

### selectors.yaml を埋める

1. 取得した `name` / `label` から、安定したセレクタを選びます。優先順位は次の通りです。
   accessibility_id → predicate（`type == '...' AND label == '...'`）→ class_chain → image → coords
2. `probe` で、その画面に存在することを確認します。

   ```bash
   .venv/bin/python -m conpass.cli probe --store "店舗名" --date 2026-11-25
   ```

3. 確認できたものだけ `verified: true` にします。`meta.app_version_verified` にアプリのバージョンを記録してください。
4. `steps` の順番を、実際の画面遷移に合わせて直します。`submit: true` は、取り消せない申込ボタンの1ステップだけに付けます。

recon の結果（XML/JSON）を私に渡してもらえれば、セレクタ定義を作成します。

## 5. フェーズ4・5：テスト実行 → 本番検証

```bash
cp config/reservations.example.yaml config/reservations.yaml   # 編集
```

```bash
.venv/bin/python -m conpass.cli validate
```

```bash
.venv/bin/python -m conpass.cli run sample --test
```

テストモードは、確認画面まで進んで **申込ボタンを押さずに** 終了します（`test_ok`）。
フェーズ5（実際の申込）は、あなたが明示的に許可したうえで行います。
`mode: production` に変更し、キャンセル可能・影響の小さい申込で1回だけ検証してください。

## 6. フェーズ6：指定日時の自動実行（launchd）

```bash
.venv/bin/python -m conpass.cli schedule sample
```

- `~/Library/LaunchAgents/local.conpass.job.<id>.plist` を生成し、`launchctl bootstrap gui/$UID` で登録します。
- launchd の分解能は1分です。そのため、`execute_at - lead_seconds`（初期値180秒）を含む分の頭に起動します。起動後にアプリを確認画面まで進め、`execute_at` まで精密に待ってからタップします。
- 実行後はジョブを自動で解除します（launchd に「年」の指定がなく、翌年に再び起動するのを防ぐため）。
- 実行中は `caffeinate -dimsu` でスリープを抑止します。

**スリープ対策（必ず読んでください）**

- Mac がスリープ中だと、launchd は **起動時刻に実行されず、復帰後に実行されます**。
  - 復帰した時点で `execute_at + late_grace_seconds`（初期値120秒）を過ぎていれば、**申込はせず `missed` として記録し、通知します**。
- 確実に起こすには、表示されるコマンドを実行してください（管理者権限が必要です）。

  ```bash
  sudo pmset schedule wakeorpoweron "MM/DD/YYYY HH:MM:SS"
  ```

- そのほかの推奨設定
  - 電源アダプタに接続する
  - 蓋を開けておく
  - 「システム設定 > ロック画面」で、実行時刻前後にログアウトしない設定にする
  - iPhone の自動ロックを「なし」にし、充電しながら USB 接続を保つ
- macOS がログアウトしていると LaunchAgent は動きません。ログインした状態を保ってください。

**時刻精度の記録**：各実行の値を DB と管理画面に記録します。

| 記録項目 | 内容 |
|---|---|
| `launch_drift_ms` | 予定した起動時刻と実際の起動時刻の差 |
| `submit_drift_ms` | `execute_at` と実際に申込タップした時刻の差 |
| `clock_offset_ms` | `sntp time.apple.com` と Mac の時計の差（500ms を超えると警告） |

## 7. フェーズ7：管理画面

```bash
.venv/bin/python -m conpass.web
```

ブラウザで http://127.0.0.1:8777 を開きます。

- 店舗名、来店日、実行日時、受付期間、モード、通知の入力
- テスト実行、本番実行（確認チェックが必須）
- launchd への登録と解除、予約のキャンセル・削除
- 実行履歴（起動誤差、申込タップ誤差、時計オフセット）
- ログとスクリーンショットの閲覧
- 未検証のセレクタ一覧（本番がブロックされている理由）

サーバーは localhost のみで待ち受けます。別のオリジンからの POST は 403 で拒否します（CSRF 対策）。

## 8. 安全設計

| 要件 | 実装 |
|---|---|
| 二重申込防止 | 店舗＋来店日を単位に SQLite で状態管理します。`succeeded` なら即終了します。ファイルロックで同時に1プロセスしか動きません |
| 申込済み判定 | `already_applied` 画面を検出したら成功として記録し、それ以上押しません |
| 成功の判定 | `complete` 画面を確認するか、予約一覧で一致した場合**のみ**成功とします |
| 結果が不明な場合 | タップ直前に `submitting` を記録します。結果が出なければ `unknown` とし、**次の申込の前に必ず予約一覧を確認**します。一覧で存在しないことを確認でき、`max_submit_attempts: 2` の場合のみ再申込します（初期値は1回） |
| 通信エラー | `network_error` 画面を検出します。申込前なら再起動して最大2回リトライし、続く場合は `network_error` で終了します |
| ログイン期限切れ | `login` 画面を検出したら停止し、手動ログインを依頼します（パスワードは扱いません） |
| CAPTCHA・追加確認 | 検出したら停止して手動対応を依頼します。回避はしません |
| 画面の変更 | 期待した画面に到達しない、または要素がない場合は `screen_changed` とし、スクショと XML を保存します |
| タイムアウト | ステップごと（初期値20秒）、結果待ち（30秒）、Appium の newCommandTimeout |
| アプリ更新 | `force_update` 画面を検出したら停止します。更新後は recon と probe で再検証してください |
| 認証情報 | 保存しません。device.yaml にあるのは UDID と Team ID だけで、ログにも出しません |
| ダイアログ | `autoAcceptAlerts: false` なので、iOS のダイアログを勝手に承認しません |

結果コード：`success` / `success_verified` / `already_applied` / `test_ok` / `skipped_already_succeeded` / `manual_required` / `screen_changed` / `network_error` / `closed` / `unknown` / `failed` / `missed` / `not_ready`

申込状態を手動でリセットする場合は次を実行します（予約一覧を目で確認してから行ってください）。

```bash
.venv/bin/python -m conpass.cli state --clear "店舗名|2026-11-25"
```

## 9. テスト

```bash
.venv/bin/python -m pytest -q
```

シミュレートしたアプリで、次のケースを検証しています。

- テストモードは申込ボタンを押さない
- 成功したあとの再実行で二重申込しない
- ログイン画面や CAPTCHA で停止する
- 結果が不明なときは一覧で確認し、確認できなければ `unknown` になり、次回は一覧の確認から始める
- 一覧で存在しないことを確認できた場合だけ再申込する
- 画面の変更を検出する
- 通信エラーが一時的ならリトライし、続けば諦める
- 受付終了を検出する
- 未検証のセレクタでは本番を拒否する
- 遅れて起動した場合は `missed` にして申込しない
- launchd の plist の内容
- 設定の検証（来店日より後の実行日時、受付期間外、重複）
- 管理画面の CRUD、本番化に確認が必要なこと、CSRF、パストラバーサル

## 10. トラブルシュート

- **WDA が起動しない**
  - 署名の Team と Bundle ID を確認し、iPhone で開発元を信頼してください。
  - `appium driver doctor xcuitest` を実行してください。
  - `data/logs/appium.log` を確認してください。
- **`xcodebuild exited with code 65`**：署名エラーです。Xcode で WebDriverAgentRunner を一度 Test 実行（⌘U）してみてください。
- **launchd が動かない**
  - `launchctl print gui/$UID/local.conpass.job.<id>` で状態を確認してください。
  - `data/logs/launchd-<id>.err.log` を確認してください。
  - ターミナルや Python に「フルディスクアクセス」の許可が必要な場合があります。

### WDA 署名エラー（`xcodebuild failed with code 65`）の切り分け

確認環境：Apple Silicon (M1) / macOS 27.2.1 / Xcode 27.2 / iOS 26.6.2 / appium-xcuitest-driver 13.4.0 / appium-webdriveragent 17.0.12。

```bash
bash scripts/check_signing.sh
```

| 症状 | 原因 | 対処 |
|---|---|---|
| `No profiles for ...xctrunner ... pass -allowProvisioningUpdates` | driver は `appium:allowProvisioningDeviceRegistration` が true のときだけ `-allowProvisioningUpdates` を付ける | `device.yaml` の `allow_provisioning_device_registration: true`（現在の初期値） |
| `No Account for Team "XXXXXXXXXX"` | Team ID の取り違え。証明書名の `(XXXXXXXXXX)` は**個人の識別子で、Team ID ではありません**。Team ID は証明書の OU 欄にあります。または Xcode のアカウント認証が期限切れになっています | `check_signing.sh` の 1 と 4 で Team ID を確認して `xcode_org_id` を直す。Xcode > Settings > Accounts でサインインし直す |
| `find-identity` が `0 valid identities` | 中間証明書（Apple WWDR G3）がない、または信頼設定に問題がある | `check_signing.sh` の 2 に出る理由を確認。WWDR G3 を https://www.apple.com/certificateauthority/ から入れる |
| Xcode GUI で WDA プロジェクトが locked | `~/.appium/node_modules` 配下は GUI での編集に向かない | 編集は不要です。driver が `DEVELOPMENT_TEAM` / `PRODUCT_BUNDLE_IDENTIFIER` を xcodebuild の引数で渡します。iOS 版 Runner は元から Automatic signing です |

`project.pbxproj` の直接編集は推奨しません。driver を更新すると上書きされるためです。
