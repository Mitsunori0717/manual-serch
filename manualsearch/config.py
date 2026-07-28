"""設定値。環境変数で上書きできる。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# APIキーなどを書いておくファイル。Windowsで環境変数を設定するのは手間なので、
# start.bat と同じ場所にこれを置くだけで設定できるようにする。
ENV_FILENAME = ".env"


def load_env_file(path: Path | str = ENV_FILENAME) -> dict[str, str]:
    """``.env`` を読んで環境変数に流し込む。返り値は読み込めた項目。

    既に環境変数側で設定されているものは上書きしない（一時的に別のキーで
    動かしたいときに、環境変数のほうが勝つようにするため）。
    """
    path = Path(path)
    if not path.is_file():
        return {}

    loaded: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):  # シェル用に書かれていても読めるように
            key = key[len("export ") :].strip()
        value = value.strip().strip('"').strip("'")
        if not key or not value:
            continue
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded

# ページのテキストがこの文字数未満なら「画像だけのページ」とみなしてOCRに回す
OCR_TEXT_THRESHOLD = int(os.environ.get("MANUAL_OCR_THRESHOLD", "16"))

# OCR時のレンダリング解像度。300dpiが精度と速度のバランスが良い
OCR_DPI = int(os.environ.get("MANUAL_OCR_DPI", "300"))

# Tesseractに渡す言語。縦書きが多いなら "jpn+jpn_vert+eng"
OCR_LANG = os.environ.get("MANUAL_OCR_LANG", "jpn+eng")

# マニュアル一覧（ライブラリ台帳）のファイル名。PDF置き場の直下に置く。
LIBRARY_FILENAME = os.environ.get("MANUAL_LIBRARY", "library.csv")


def save_env_file(values: dict[str, str], path: Path | str = ENV_FILENAME) -> None:
    """``.env`` の項目を書き換える。他の行はそのまま残す。

    値が空文字のものは、その項目ごと削除する（APIキーの取り消し用）。
    """
    path = Path(path)
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    remaining = dict(values)
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        key = stripped.partition("=")[0].strip() if "=" in stripped else ""
        if not stripped.startswith("#") and key in remaining:
            value = remaining.pop(key)
            if value:  # 空なら行ごと消す
                out.append(f"{key}={value}")
        else:
            out.append(line)

    for key, value in remaining.items():
        if value:
            out.append(f"{key}={value}")

    path.write_text("\n".join(out).strip() + "\n", encoding="utf-8")
    # 書き込んだ内容をこのプロセスにも反映する（再起動なしで効かせるため）
    for key, value in values.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)


def mask_secret(value: str) -> str:
    """APIキーを画面に出すための伏せ字。"""
    if not value:
        return ""
    return f"{value[:6]}…{value[-4:]}" if len(value) > 14 else "…" * 4


@dataclass(frozen=True)
class AiConfig:
    """ChatGPT連携の設定。APIキーが無ければ相談機能だけ無効になる。"""

    api_key: str = ""
    model: str = "gpt-4o-mini"
    base_url: str = ""
    timeout: float = 60.0

    @property
    def enabled(self) -> bool:
        # ローカルのLLMサーバー（Ollama など）はキーを要求しないので、
        # 接続先が指定されていればキーが空でも使えるものとして扱う。
        return bool(self.api_key or self.base_url)

    @property
    def is_local(self) -> bool:
        return bool(self.base_url) and any(
            host in self.base_url for host in ("localhost", "127.0.0.1", "0.0.0.0", "::1")
        )

    @classmethod
    def from_env(cls) -> "AiConfig":
        return cls(
            api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
            # 使いたいモデルに合わせて MANUAL_AI_MODEL で差し替える
            model=os.environ.get("MANUAL_AI_MODEL", "gpt-4o-mini").strip(),
            # Azure OpenAI や社内ゲートウェイを使う場合はここを向ける
            base_url=os.environ.get("OPENAI_BASE_URL", "").strip(),
            timeout=float(os.environ.get("MANUAL_AI_TIMEOUT", "60")),
        )


@dataclass(frozen=True)
class Config:
    """検索対象のルートディレクトリと索引DBの場所。"""

    root: Path
    db_path: Path
    ai: AiConfig = AiConfig()

    @property
    def library_path(self) -> Path:
        """マニュアル一覧（CSV）の場所。"""
        return self.root / LIBRARY_FILENAME

    @classmethod
    def from_env(cls, root: str | os.PathLike[str] | None = None,
                 db_path: str | os.PathLike[str] | None = None) -> "Config":
        resolved_root = Path(root or os.environ.get("MANUAL_ROOT", "manuals")).expanduser().resolve()
        resolved_db = Path(db_path or os.environ.get("MANUAL_DB", "index.db")).expanduser().resolve()
        return cls(root=resolved_root, db_path=resolved_db, ai=AiConfig.from_env())
