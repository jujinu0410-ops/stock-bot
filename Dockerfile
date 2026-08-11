# GCP Cloud Run Job 전용 Python 3.11 슬림 컨테이너
FROM python:3.11-slim

WORKDIR /app

# 타임존을 한국 표준시(KST)로 설정
ENV TZ=Asia/Seoul
RUN apt-get update && apt-get install -y tzdata && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone && \
    rm -rf /var/lib/apt/lists/*

# 패키지 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스코드 전체 복사
COPY . .

# 실행 명령어
CMD ["python", "main.py"]
