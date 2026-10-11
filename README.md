# Z-Tipo Append Helper

An independent helper extension for **Z-Tipo** on **sd-webui-forge-neo**.

Z-Tipo本体を改造せず、TIPO生成結果を既存のPositive Promptへ追加するための補助拡張です。

## Requirements

- sd-webui-forge-neo
- z-tipo-extension

Z-Tipo本体がインストール済みである必要があります。

## Features

- Z-Tipo本体を無改造で使用
- Tag Prompt / Natural Language PromptをTIPO生成入力として使用
- 既存Positive Promptを維持
- TIPO生成結果を既存Positive Promptの末尾へ追加
- 重複タグ除去
- 表記揺れを考慮した重複判定
- Ban Tags対応
- 不正な正規表現を安全に処理
- Z-Tipo本体で現在選択されているモデルを使用
- 設定保存
- UIの開閉状態を保存
- txt2img / img2img対応

## Duplicate Tag Handling

以下のような表記は、同一タグとして扱われます。

    blue_hair
    blue hair
    (blue hair:1.2)

既存Positive Promptに同じタグがある場合は、既存側の表記を維持し、TIPO生成結果側の重複タグを追加しません。

例:

Existing Positive Prompt:

    1girl, hatsune miku, aqua hair

TIPO result:

    cinematic lighting, aqua hair, rim light

Result:

    1girl, hatsune miku, aqua hair, cinematic lighting, rim light

## Ban Tags

Ban Tagsに不正な正規表現が含まれていても、エラーで処理全体が停止しないよう安全に扱います。

## Model Selection

このHelper独自のモデル選択欄はありません。

Z-Tipo本体で現在選択されているモデルを使用します。

## Installation

先にZ-Tipo本体をインストールしてください。

その後、このリポジトリを `sd-webui-forge-neo/extensions/` にCloneします。

    git clone https://github.com/Me50Geb1/z-tipo-append-helper.git

配置例:

    sd-webui-forge-neo/
    └─ extensions/
       ├─ z-tipo-extension/
       └─ z-tipo-append-helper/
          ├─ javascript/
          ├─ scripts/
          ├─ README.md
          └─ style.css

インストール後、Forge Neoを再起動してください。

## Settings

ユーザー設定はローカルの `settings.json` に保存されます。


## Compatibility

Forge Neo 2.30でのUI表示を確認済みです。重み付きPromptの解析関数の移動に対応し、
旧版の解析関数にもフォールバックします。TIPO推論の実動作は未確認です。
Z-Tipo本体とその推論環境は別途必要です。

Main target:

- sd-webui-forge-neo
- z-tipo-extension

## Notes

This extension was created with the assistance of ChatGPT.

The repository owner may not be able to answer technical questions, provide support, or respond to implementation-related inquiries.

この拡張はChatGPTの支援により作成されています。

リポジトリ所有者は技術的な質問、サポート、実装に関する問い合わせには対応できない場合があります。

## Changelog

See [CHANGELOG.md](CHANGELOG.md).
