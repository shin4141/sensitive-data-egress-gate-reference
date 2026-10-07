# Sensitive Data Egress Gate — 最小の参照実装

**Current: one credential → potentially ALL**

**Target: one credential → bounded**

**This repository is a reference implementation, not a production security product.**

商用Design・成果物サンプルはこちら：[Sensitive Data Egress Gate](https://shin4141.github.io/sensitive-data-egress-gate/)

認証情報が1つ悪用されても、取得できる量を条件で区切る。その設計思想を、架空データで数秒以内に確認できるCLIです。
通常は **500件/回・直近24時間で2,000件**。大量取得には別の承認者を要求し、全量・高影響の取得は、通知・待機・独立承認・最終責任者の承認がそろった専用経路（Escalation Multisig Gate）へ進みます。

これは参照実装であり、本番のセキュリティ製品ではありません。[English](README.en.md)

## このリポジトリが扱う問い

このリポジトリは、**個人情報セキュリティの設計**、**1つの認証情報が侵害された後の最大取得量（maximum data extraction）**、**one-credential blast radius**、**AI agent / automationによるdata egress**、そして**停止・承認・通常業務への復帰（stop / approval / recovery boundaries）**を扱う最小モデルです。

問いは単純です。

> 1つのcredentialや通常権限が悪用されたとき、機微データは最大どこまで出られるのか。どこで止まり、誰の承認で、どう通常業務へ戻るのか。

侵入そのものを防ぐ仕組みの代替ではありません。侵入・誤用・自動化の誤動作が起きても、**到達可能な損失範囲を有限に設計する**ための参照実装です。

## すぐ動かす

Python **3.12以降**とGitを使います。追加ライブラリ・pip install・認証情報は不要です。実行時のネットワークアクセスもありません。
公開リポジトリをcloneし、そのフォルダで実行します。

```sh
git clone https://github.com/shin4141/sensitive-data-egress-gate-reference.git
cd sensitive-data-egress-gate-reference
python3 -m sdeg
python3 -m unittest discover -s tests -v
```

短い実行例です。完全な出力は [examples/cli-output.txt](examples/cli-output.txt) に保存しています。

```text
Current: 1 credential -> ALL (12,000 fictional records)
Target:  1 credential -> bounded (500/request; 2,000/rolling 24h)

Normal / 500                    PASS | released=500
Normal / 501                    BLOCK
Rolling 24h / 2,000              PASS | cumulative=2,000
Rolling 24h / 2,001              INDEPENDENT_APPROVAL_REQUIRED
New destination                 APPROVAL_INVALIDATED
ALL / normal path               BLOCK
ALL / Owner alone               BLOCK
ALL / before WAIT completes      BLOCK

Request -> Notify -> WAIT -> Independent Seat -> Owner Seat -> Release
ALL / complete chain            PASS | released=12,000
```

WAITは模擬時計を進めます。実時間の待機や実際の通知送信は行いません。
`PASS`の取得は架空レコードの配列を返し、`BLOCK`は0件を返します。表示だけの判定ではありません。

## 固定サンプルで確認できる条件

| 操作・条件 | 判定 |
| --- | --- |
| 通常経路で500件 / 501件 | 許可 / 停止して上位経路へ |
| 直近24時間の合計2,000件 / 2,001件 | 許可 / 独立承認が必要 |
| 大量取得、申請者自身の承認、Ownerを独立承認者として使う | 別の独立承認者が必要 |
| 新規送信先、承認後の対象範囲・件数・申請者・取得許可の種類の変更 | 旧承認を失効。新規送信先は停止 |
| 全量、または高影響の取得 | 通常・大量取得経路では停止。段階的な複数承認へ |
| Owner単独、通知なし、待機完了前、独立承認なし、最終承認なし | 停止 |
| 正式な申請 → 全登録者へ通知 → 待機 → 独立承認 → Owner承認 → 取得 | 許可 |
| 承認期限切れ、別申請への再利用、同じ取得許可の再実行 | 停止 |

取得量はCSV・API・全件取得、対象範囲、送信先をまたいで、同一の申請者ごとに合算します。
直近24時間は `(現在時刻−24時間, 現在時刻]` です。同じレコードの再取得も時間累積に数えます。
直近24時間の取得量、または起動中に取得済みの異なる架空レコードの数が **10,000件以上**なら、専用の承認経路が必要です。日をまたぐ分割取得でもこの条件を省略できません。

全量は1つの架空対象範囲の **12,000件**。待機は **60秒**、承認期限は申請・変更から **300秒**です。
承認は対象の申請・範囲・件数・送信先・申請者・取得許可の種類・取得経路・取得位置に固定し、変更時は通知と待機もやり直します。
この例は独立承認者1名と最終責任者1名を使います。人数・Owner構成・各数値は推奨値や唯一の正解ではありません。

## 実装を追う

- [sdeg/gate.py](sdeg/gate.py)：取得条件、申請に固定された承認、rolling累積、専用承認経路。
- [sdeg/demo.py](sdeg/demo.py)：Current → Targetと短いCLI実行例。
- [tests/test_gates.py](tests/test_gates.py)：境界値、順序省略、変更・失効・再利用、同時取得を検証。
- [.github/workflows/ci.yml](.github/workflows/ci.yml)：Python 3.12 / 3.14でテストとCLIを実行。読み取り権限のみ。

## 確認できる範囲

架空の申請者・承認者IDとロールは、信頼できる呼び出し元から渡される前提です。
実際の認証・暗号署名・通知配信・永続化・外部送信は実装していません。プロセス内のコードやメモリを自由に変更できる攻撃者は、このモデルの対象外です。
状態と操作記録はメモリ内にあり、再起動で失われます。取得処理は1プロセス内で排他制御しています。分散システムや本番運用の保証を示すものではありません。
データは `FICTIONAL/...` という生成IDだけで、実企業・実個人の情報は使用していません。

## 信頼境界

この参照実装は、実顧客データ、本番認証情報、広範な内部アクセスを必要としません。
商用の設計書作成（Design）も、境界を定義するために必要な最小限の情報だけを扱い、双方の不要な法的・プライバシー・運用リスクを抑える形にします。
外部の確認担当者については、[公開OSSで受理された修正履歴](https://github.com/shin4141/shin4141/blob/main/MERGE_PORTFOLIO.md)から、機微なシステムを開示する前に評価できます。

## コードは見せる。判断は商品として残す。

ここで公開するのは、固定の架空条件を実行する最小モデルです。
実企業の閾値を決めるロジック、顧客固有の承認する立場の設計判断、業務に不要な制御を引き算する判断手順、商用案件の例外設計は含めません。
実案件の設計値・責任分担・対象範囲は各社要件を確認して決めます。
[Sensitive Data Egress Gate](https://shin4141.github.io/sensitive-data-egress-gate/) の成果物設計と実装後の確認につながる参照例です。

License: [MIT](LICENSE)
