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


DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_LOCAL_BASE_URL = "http://localhost:11434/v1"


@dataclass(frozen=True)
class AiConfig:
    """AI相談の設定。

    OpenAI と ローカルサーバーの設定を**両方とも**保持し、``provider`` でどちらを
    使うかを切り替える。片方を設定するともう片方が消える、という作りにすると
    「今日はローカル、急ぎのときはOpenAI」のような使い分けができないため。
    """

    provider: str = "openai"  # "openai" か "local"
    api_key: str = ""
    openai_model: str = DEFAULT_OPENAI_MODEL
    local_base_url: str = ""
    local_model: str = ""
    timeout: float = 60.0

    # -------------------------------------------------- 選ばれている側の設定
    @property
    def is_local(self) -> bool:
        return self.provider == "local"

    @property
    def model(self) -> str:
        return self.local_model if self.is_local else self.openai_model

    @property
    def base_url(self) -> str:
        return self.local_base_url if self.is_local else ""

    @property
    def client_api_key(self) -> str:
        # ローカルサーバーはキーを見ないが、SDKが空を許さないので埋める。
        # OpenAIのキーをローカルサーバーへ送らないよう、ここで切り離す。
        return "local" if self.is_local else self.api_key

    @property
    def enabled(self) -> bool:
        return bool(self.local_base_url and self.local_model) if self.is_local else bool(self.api_key)

    @property
    def can_probe(self) -> bool:
        """接続先を確かめられる状態か。

        モデルが未入力でも、接続先さえ分かればサーバーに「何が入っているか」を
        聞ける。その一覧から選ばせたいので、``enabled`` とは分けて持つ。
        """
        return bool(self.local_base_url) if self.is_local else bool(self.api_key)

    @property
    def missing(self) -> str:
        """使える状態になるために足りないもの。空文字なら不足なし。"""
        if self.is_local:
            if not self.local_base_url:
                return "接続先"
            if not self.local_model:
                return "モデル"
            return ""
        return "" if self.api_key else "APIキー"

    @property
    def where(self) -> str:
        """画面に出す接続先の呼び名。"""
        if not self.is_local:
            return "OpenAI"
        return "ローカル"

    # -------------------------------------------------- 使える側があるか
    @property
    def openai_ready(self) -> bool:
        return bool(self.api_key)

    @property
    def local_ready(self) -> bool:
        return bool(self.local_base_url and self.local_model)

    @classmethod
    def from_env(cls) -> "AiConfig":
        base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
        provider = os.environ.get("MANUAL_AI_PROVIDER", "").strip().lower()
        if provider not in ("openai", "local"):
            # 以前は接続先を入れた時点でローカル扱いだったので、その設定も読めるようにする
            provider = "local" if base_url else "openai"

        return cls(
            provider=provider,
            api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
            openai_model=os.environ.get("MANUAL_AI_MODEL", DEFAULT_OPENAI_MODEL).strip()
            or DEFAULT_OPENAI_MODEL,
            local_base_url=base_url,
            local_model=os.environ.get("MANUAL_LOCAL_MODEL", "").strip(),
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
