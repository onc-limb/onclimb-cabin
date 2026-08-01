#!/usr/bin/env python3
"""study/ 配下への AI によるファイル作成・編集をブロックする PreToolUse フック。

study/ は「AI に絶対にコードを書かせない」学習用ディレクトリ。
Write / Edit / MultiEdit / NotebookEdit がこのディレクトリ配下を対象にした場合、
exit code 2 で拒否し、理由を stderr 経由で Claude に伝える。
"""
import json
import os
import sys


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        # 入力が壊れている場合はブロックしない（フックの誤作動で他の作業を止めない）
        return 0

    tool_input = data.get("tool_input") or {}
    path = (
        tool_input.get("file_path")
        or tool_input.get("notebook_path")
        or ""
    )
    if not path:
        return 0

    # 守る study/ はこのスクリプトの位置から解決する。
    # CLAUDE_PROJECT_DIR は projects/ 配下の別 git リポジトリで作業中に
    # そちらへ解決されることがあり、その値を基準にすると誤った study/ を
    # 指してしまうため使わない（.claude/hooks/this.py -> ルートへ 3 階層上る）。
    project = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    study = os.path.join(project, "study")

    if os.path.isabs(path):
        target = os.path.abspath(path)
    else:
        # 相対パスは実際の作業ディレクトリ基準で解決する
        target = os.path.abspath(path)

    if target == study or target.startswith(study + os.sep):
        sys.stderr.write(
            "🚫 study/ は学習用の『AI がコードを書かない』ディレクトリです。\n"
            "このディレクトリ配下のファイルは Write / Edit で作成・編集できません。\n"
            "コードはユーザー自身が書いてください。"
            "AI は言葉での解説・質問への回答のみを行います。\n"
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
