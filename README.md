# ワールド・ニュースデスク

金融・経済・地政学・科学・量子の世界のニュースを自動で集め、日本語に翻訳して一覧表示するサイトです。主要な株価指数・為替・金利・商品・暗号資産のチャートも見られます。

- **収集**: 世界各地の報道機関・中央銀行・研究機関の RSS 約 60 本 + [GDELT](https://www.gdeltproject.org/)(100 以上の言語のニュースを索引する無料 API。英語以外の地域を補う)
- **翻訳**: Claude API または DeepL API で見出しと要約を日本語化(翻訳済みはキャッシュし、二度翻訳しない)
- **チャート**: 日経平均・S&P 500・ナスダック・NYダウ・DAX・FTSE・ハンセン・上海総合・ドル円・ユーロ円・ユーロドル・米10年債利回り・金・WTI・ビットコインの日足(最大1年)
- **公開**: GitHub Actions が毎時実行し、GitHub Pages に静的サイトとして公開(サーバー不要)

## 仕組み

```
GitHub Actions (毎時)
  └─ python -m newsdesk
       ├─ RSS / GDELT を取得 → HTML除去・URL検証 → 重複排除(72時間分を保持)
       ├─ 未翻訳の記事だけ Claude / DeepL で日本語化(1回の上限あり)
       ├─ 市場データを取得(Yahoo Finance、失敗時は Stooq)
       └─ site/data/*.json を出力
  └─ site/ を GitHub Pages にデプロイ
```

記事・翻訳キャッシュは Actions のキャッシュに保存し、リポジトリには毎時コミットしません。

## セットアップ

1. **GitHub Pages を有効化**: リポジトリの Settings → Pages → Source を「GitHub Actions」にする
2. **翻訳用 API キーを登録**: Settings → Secrets and variables → Actions → **Secrets** に、どちらか(または両方)を登録
   - `ANTHROPIC_API_KEY`: [Claude Console](https://console.anthropic.com/) で発行
   - `DEEPL_API_KEY`: [DeepL API](https://www.deepl.com/pro-api) で発行(無料版キーは末尾が `:fx`)
   - どちらも未登録なら翻訳せず原文のまま表示します
3. **(任意)設定値**: 同じ画面の **Variables** で調整できます

   | 変数 | 既定値 | 説明 |
   |---|---|---|
   | `TRANSLATOR` | `auto` | `claude` / `deepl` / `none`。`auto` は Claude → DeepL の順でキーがある方 |
   | `TRANSLATE_MODEL` | `claude-opus-5-5` | Claude のモデル。費用を抑えるなら `claude-haiku-5-5` |
   | `TRANSLATE_EFFORT` | `low` | Claude の effort(`low`〜`max`) |
   | `MAX_TRANSLATE_PER_RUN` | `200` | 1 回(1 時間)に新規翻訳する記事数の上限。費用の上限になる |

4. **初回実行**: Actions タブ →「ニュース更新・公開」→ Run workflow。以後は毎時自動で更新されます
5. 実行後、サイトの「取得状況」タブで、どの取得元が成功・失敗したか確認できます

### 翻訳費用の目安

1 記事あたり入力約 150 トークン・出力約 150 トークン(+ 思考トークン)として、上限の 200 記事/時を毎時翻訳した場合の概算です。

| エンジン | 1時間あたり | 1か月あたり |
|---|---|---|
| Claude Opus 5.5(既定) | 約 $0.7 | 約 $500 |
| Claude Haiku 5.5 | 約 $0.02 | 約 $13 |
| DeepL | 約 6 万文字 | 無料枠(月 50 万文字)は数時間で超過 |

実際の新着記事数はこれより少なくなることが多いです。`MAX_TRANSLATE_PER_RUN` を下げるか、ワークフローの `cron` を間引く(例: `17 */3 * * *` で3時間ごと)と費用を抑えられます。

## RSS が取得できない場合

「取得状況」タブで `HTTP 403` や「フィードではない応答(ブロックされた可能性)」が出ている取得元は、ボット対策で拒否されています。その場合は次のような API キーが必要なサービスへの切り替えを検討してください(要実装)。

- ニュース全般: [NewsAPI](https://newsapi.org/)、[GNews](https://gnews.io/)、[NewsData.io](https://newsdata.io/)
- 金融ニュース: [Finnhub](https://finnhub.io/)、[Alpha Vantage News](https://www.alphavantage.co/)、[Marketaux](https://www.marketaux.com/)
- 市場データ: [Twelve Data](https://twelvedata.com/)、[Alpha Vantage](https://www.alphavantage.co/)、[FRED](https://fred.stlouisfed.org/docs/api/)(米経済指標)

取得元は `newsdesk/config/feeds.json`、チャートの銘柄は `newsdesk/config/markets.json` で追加・削除できます(`"enabled": false` で一時停止)。

## セキュリティ対策

**サイト(フロントエンド)**
- 静的ファイルのみ。サーバー処理・データベース・ログイン・フォーム・Cookie がなく、攻撃対象が最小
- 厳格な Content-Security-Policy: 自サイト以外のスクリプト・スタイル・通信・埋め込みを全面禁止、インラインスクリプト禁止
- Trusted Types を強制(`require-trusted-types-for 'script'`)し、`innerHTML` 等での HTML 文字列挿入をブラウザレベルで禁止。外部由来の文字列は `textContent` でのみ描画
- 外部ライブラリ・CDN・外部フォントを使わない(チャートも自前の SVG)ので、サプライチェーン経由の改ざんがない
- リンクは `http(s)` のみ許可し、`rel="noopener noreferrer nofollow"`、Referrer は送らない
- Cloudflare Pages / Netlify で公開する場合は `site/_headers` により HSTS・X-Frame-Options・nosniff・Permissions-Policy・COOP/CORP なども付与(GitHub Pages はヘッダーを設定できないため meta CSP で代替)

**収集処理(バックエンド)**
- 取得した HTML はタグを全て除去しプレーンテキスト化。制御文字・双方向制御文字も除去
- URL は `http(s)` の絶対 URL のみ許可(`javascript:`・`data:`・認証情報付き URL を拒否)
- HTTP はタイムアウト・サイズ上限(5MB)・リダイレクト上限つき
- 翻訳プロンプトでは記事本文をデータとして扱い、記事中の指示に従わないよう明示(プロンプトインジェクション対策)。翻訳結果も同じ無害化を通す
- API キーは GitHub Secrets のみに保存し、コード・ログに出さない

**GitHub Actions**
- ワークフローの権限は最小(既定は権限なし、ジョブごとに必要な分だけ付与)
- 使用する Actions はコミット SHA で固定し、Dependabot で更新を追跡
- `persist-credentials: false` でトークンをディスクに残さない

## ローカルで動かす

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export ANTHROPIC_API_KEY=...        # 任意。なければ翻訳なし
python -m newsdesk                  # state/ と site/data/ を生成
python -m http.server -d site 8000  # http://localhost:8000 を開く
python -m pytest                    # テスト
```

## 注意

見出し・要約の著作権は各報道機関にあります。本サイトは見出しと短い要約、原文へのリンクのみを表示します。市場データは遅延・欠損することがあり、投資判断の根拠にはなりません。Yahoo Finance・Stooq のデータ取得は公式に公開された API ではないため、将来使えなくなる可能性があります。
