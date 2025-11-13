FROM python:3.12-slim

# 필수 패키지 설치 및 AWS CLI 설치 (알파벳순 정렬)
RUN apt-get update && apt-get install -y \
    cmake \
    curl \
    gobject-introspection \
    graphviz \
    libcairo2-dev \
    libgirepository1.0-dev \
    libgraphviz-dev \
    libgtk-3-dev \
    pkg-config \
    libgirepository-2.0-dev \
    unzip \
    && rm -rf /var/lib/apt/lists/* \
    # AWS CLI v2 설치 (아키텍처에 따라 자동 선택)
    && ARCH=$(uname -m) \
    && if [ "$ARCH" = "aarch64" ]; then \
        curl "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o "awscliv2.zip"; \
    else \
        curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"; \
    fi \
    && unzip awscliv2.zip \
    && ./aws/install \
    && rm -rf awscliv2.zip aws

# 작업 디렉토리 설정
WORKDIR /app

# Python 의존성 파일 복사 및 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 파일 복사
COPY . .

# 가상환경 디렉토리 생성 (이미지 빌드 시에는 생성하지 않음)
# RUN mkdir -p virtual_env

RUN broadwayd :5 &
RUN sleep 5

# 스크립트 실행 권한 부여
RUN chmod +x docker_web_start.sh

# 환경 변수 설정
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:0

# 기본 명령어
CMD ["/bin/bash"]
