"""設定値。環境変数で上書きできる。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# ページのテキストがこの文字数未満なら「画像だけのページ」とみなしてOCRに回す
OCR_TEXT_THRESHOLD = int(os.environ.get("MANUAL_OCR_THRESHOLD", "16"))

# OCR時のレンダリング解像度。300dpiが精度と速度のバランスが良い
OCR_DPI = int(os.environ.get("MANUAL_OCR_DPI", "300"))

# Tesseractに渡す言語。縦書きが多いなら "jpn+jpn_vert+eng"
OCR_LANG = os.environ.get("MANUAL_OCR_LANG", "jpn+eng")

# マニュアル一覧（ライブラリ台帳）のファイル名。PDF置き場の直下に置く。
LIBRARY_FILENAME = os.environ.get("MANUAL_LIBRARY", "library.csv")


@dataclass(frozen=True)
class AiConfig:
    """ChatGPT連携の設定。APIキーが無ければ相談機能だけ無効になる。"""

    api_key: str = ""
    model: str = "gpt-4o-mini"
    base_url: str = ""
    timeout: float = 60.0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

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
