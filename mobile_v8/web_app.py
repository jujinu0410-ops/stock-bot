from __future__ import annotations

import os
import re

import google.auth
from flask import Flask, render_template_string, request
from google.auth.transport.requests import AuthorizedSession

app = Flask(__name__)

PAGE = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>V8 종목 분석</title>
  <style>
    body { margin:0; background:#f3f4f6; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif; color:#111827; }
    main { max-width:520px; margin:0 auto; padding:42px 18px; }
    .card { background:white; border:1px solid #e5e7eb; border-radius:16px; padding:22px; box-shadow:0 1px 3px rgba(0,0,0,.05); }
    h1 { font-size:24px; margin:0 0 22px; }
    label { display:block; font-size:14px; color:#4b5563; margin-bottom:8px; }
    input { width:100%; box-sizing:border-box; font-size:20px; padding:14px; border:1px solid #d1d5db; border-radius:10px; outline:none; }
    button { width:100%; margin-top:14px; padding:14px; border:0; border-radius:10px; font-size:17px; font-weight:700; background:#111827; color:white; }
    button:disabled { opacity:.65; }
    .ok { margin-top:18px; padding:14px; background:#ecfdf5; border-radius:10px; color:#065f46; line-height:1.55; }
    .err { margin-top:18px; padding:14px; background:#fef2f2; border-radius:10px; color:#991b1b; line-height:1.55; }
    .hint { margin-top:10px; font-size:13px; color:#6b7280; }
  </style>
</head>
<body><main><div class="card">
  <h1>V8 종목 분석</h1>
  <form id="run_form" method="post" action="/run">
    <label for="stock_input">종목명 또는 종목코드</label>
    <input id="stock_input" name="stock_input" placeholder="예: SKC 또는 011790" autocomplete="off" autofocus required>
    <button id="run_button" type="submit">분석 실행</button>
  </form>
  <div class="hint">분석을 시작한 뒤 페이지를 닫아도 됩니다. 완료 후 Gmail로 결과를 보냅니다.</div>
  {% if message %}<div class="ok">{{ message }}</div>{% endif %}
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
</div></main>
<script>
  document.getElementById('run_form').addEventListener('submit', function () {
    const button = document.getElementById('run_button');
    button.disabled = true;
    button.textContent = '분석 요청 중…';
  });
</script>
</body></html>"""


def _clean_stock_input(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("종목명 또는 종목코드를 입력해 주세요.")
    if len(value) > 80:
        raise ValueError("입력값이 너무 깁니다.")
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError("사용할 수 없는 문자가 포함되어 있습니다.")
    return value


def _execute_job(stock_input: str) -> str:
    project = os.environ.get("GCP_PROJECT", "").strip()
    region = os.environ.get("GCP_REGION", "").strip()
    job_name = os.environ.get("V8_JOB_NAME", "").strip()
    if not project or not region or not job_name:
        raise RuntimeError("Cloud Run Job 설정이 완료되지 않았습니다.")

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    session = AuthorizedSession(credentials)
    url = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{job_name}:run"
    payload = {
        "overrides": {
            "containerOverrides": [
                {
                    "env": [
                        {"name": "STOCK_INPUT", "value": stock_input}
                    ]
                }
            ]
        }
    }
    response = session.post(url, json=payload, timeout=30)
    if response.status_code >= 300:
        raise RuntimeError(f"분석 Job 시작 실패 ({response.status_code})")
    data = response.json()
    return str(data.get("name") or "started")


@app.get("/")
def index():
    return render_template_string(PAGE, message=None, error=None)


@app.post("/run")
def run_analysis():
    try:
        stock_input = _clean_stock_input(request.form.get("stock_input", ""))
        _execute_job(stock_input)
        message = f"{stock_input} 분석을 시작했습니다. 완료 후 Gmail로 결과를 보냅니다. 이 페이지는 닫아도 됩니다."
        return render_template_string(PAGE, message=message, error=None), 202
    except ValueError as exc:
        return render_template_string(PAGE, message=None, error=str(exc)), 400
    except Exception as exc:
        app.logger.exception("Failed to start V8 job")
        return render_template_string(PAGE, message=None, error=f"분석 시작에 실패했습니다: {exc}"), 500


@app.get("/healthz")
def healthz():
    return {"ok": True}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
