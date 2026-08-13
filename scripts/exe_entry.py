"""PyInstaller用のトップレベルスクリプト。

パッケージ内のモジュールを直接エントリにすると相対importが壊れるので、
この薄いスクリプトを挟む。中身は launcher に丸投げ。
"""

from manualsearch.launcher import entry

if __name__ == "__main__":
    entry()
