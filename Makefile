.PHONY: setup index serve sample test clean
.DEFAULT_GOAL := serve

VENV        ?= .venv
PY          := $(VENV)/bin/python
MANUAL_ROOT ?= manuals
MANUAL_DB   ?= index.db
PORT        ?= 8000
export MANUAL_DB

$(PY):
	python3 -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r requirements.txt

setup: $(PY)  ## 仮想環境を作って依存関係を入れる
	$(PY) -m manualsearch doctor

sample: $(PY)  ## 動作確認用のサンプルPDFを作る
	$(PY) scripts/make_sample_manuals.py $(MANUAL_ROOT)

index: $(PY)  ## PDFを索引に取り込む（増えたぶんだけ）
	$(PY) -m manualsearch index $(MANUAL_ROOT)

serve: index  ## 索引を更新してから検索画面を起動する
	$(PY) -m manualsearch serve $(MANUAL_ROOT) --port $(PORT)

test: $(PY)  ## テストを流す
	$(PY) -m pip install --quiet pytest httpx
	$(PY) -m pytest -q

clean:  ## 索引と仮想環境を消す（PDFは消さない）
	rm -rf $(VENV) $(MANUAL_DB) $(MANUAL_DB)-wal $(MANUAL_DB)-shm
