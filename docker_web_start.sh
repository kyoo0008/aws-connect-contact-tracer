#!/bin/bash -e

# Copyright © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms and the SOW between the parties dated [March 18, 2024].

###
# Docker 환경에서 웹 서버를 실행하는 스크립트
#

# VENV_DIR="virtual_env"

# # virtual_env 디렉토리 생성 (데이터 저장용만)
# # mkdir -p $VENV_DIR

# if [ ! -d "$VENV_DIR" ]; then
#   python3 -m venv "$VENV_DIR"
#   echo -e "\n\"$VENV_DIR\" Python 가상환경이 생성되었습니다.\n"

#   # 가상환경 활성화
#   source "$VENV_DIR/bin/activate"

#   if [ -f "$REQUIREMENTS_FILE" ]; then
#     pip install -r $REQUIREMENTS_FILE
#   else
#     echo "$REQUIREMENTS_FILE 파일이 존재하지 않습니다."
#     exit 1
#   fi
# else
#   # 가상환경 활성화
#   source "$VENV_DIR/bin/activate"
# fi
# echo "✅ Docker 이미지에 이미 설치된 패키지를 사용합니다."
# echo "✅ GTK+3는 Docker 이미지에 포함되어 있습니다."

# AWS Profile 값 확인
profile_value=$(aws configure list | awk -F: '/profile/ {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}')
region_value=$(aws configure list | awk -F: '/region/ {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}')

if [[ "$profile_value" == "<not" ]]; then
  echo "⚠️  AWS CLI에서 Profile이 설정되지 않았습니다!"
  echo "👉 AWS_PROFILE 환경 변수를 설정하세요."
else
  echo "✅ AWS Profile이 설정되었습니다: $profile_value"
fi

# AWS SSO 로그인 상태 확인
aws sts get-caller-identity >/dev/null 2>&1
if [ $? -eq 0 ]; then
  echo "✅ AWS SSO에 로그인되어 있습니다."
else
  echo "❌ AWS SSO에 로그인되어 있지 않습니다!"
  exit 1
fi

# AWS 계정 정보 환경 변수 설정
if [[ $region_value == "ap-northeast-2" ]]; then
  account_id=$(aws sts get-caller-identity --query "Account" --output text)
  case "$account_id" in
    "590183945142") export INSTANCE_ALIAS="kal-servicecenter" && export AWS_REGION="ap-northeast-2" && export ENV=prd;;
    "637423289860") export INSTANCE_ALIAS="kal-servicecenter-dev" && export AWS_REGION="ap-northeast-2" && export ENV=dev;;
    "637423576272") export INSTANCE_ALIAS="kal-servicecenter-stg" && export AWS_REGION="ap-northeast-2" && export ENV=stg;;
    *) echo "❌ 지원되지 않는 AWS 계정 ID: $account_id" && exit 1 ;;
  esac
else
  account_id=$(aws sts get-caller-identity --query "Account" --output text)
  case "$account_id" in
    "590183945142") export INSTANCE_ALIAS="kal-servicecenter-an1" && export AWS_REGION="ap-northeast-1" && export ENV=prd;;
    "637423289860") export INSTANCE_ALIAS="kal-servicecenter-an1-dev" && export AWS_REGION="ap-northeast-1" && export ENV=dev;;
    "637423576272") export INSTANCE_ALIAS="kal-servicecenter-an1-stg" && export AWS_REGION="ap-northeast-1" && export ENV=stg;;
    *) echo "❌ 지원되지 않는 AWS 계정 ID: $account_id" && exit 1 ;;
  esac
fi

# Instance ID 가져오기
export INSTANCE_ID=$(aws connect list-instances --query "InstanceSummaryList[?InstanceAlias=='$INSTANCE_ALIAS'].Id" --output text)

if [ -z "$INSTANCE_ID" ]; then
  echo "❌ 입력한 Alias의 Amazon Connect 인스턴스를 찾을 수 없습니다!"
  exit 1
else
  echo "✅ '$INSTANCE_ALIAS'의 인스턴스 ID는 '$INSTANCE_ID' 입니다."
fi

# 사용 가능한 포트 찾기 함수
find_available_port() {
  local start_port=${1:-5000}
  local max_port=${2:-5100}

  for port in $(seq $start_port $max_port); do
    if ! nc -z localhost $port 2>/dev/null; then
      echo $port
      return 0
    fi
  done

  # nc가 없는 경우 Python으로 확인
  python3 -c "
import socket
for port in range($start_port, $max_port + 1):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('0.0.0.0', port))
        s.close()
        print(port)
        exit(0)
    except OSError:
        continue
exit(1)
"
}

# 사용 가능한 포트 찾기
WEB_PORT=$(find_available_port 5000 5100)

if [ -z "$WEB_PORT" ]; then
  echo "❌ 사용 가능한 포트를 찾을 수 없습니다 (5000-5100 범위)."
  exit 1
fi

export FLASK_PORT=$WEB_PORT

# 웹 서버 시작
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🌐 웹 서버를 포트 $WEB_PORT 에서 시작합니다..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "   브라우저에서 다음 주소로 접속하세요:"
echo "   👉 http://localhost:$WEB_PORT"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 백그라운드에서 Flask 웹 애플리케이션 실행
python3 web_app.py &
FLASK_PID=$!

# 서버가 완전히 시작될 때까지 대기
echo "⏳ 웹 서버 시작 대기 중..."
sleep 3

# 호스트에 브라우저 열기 신호 파일 생성
echo "$WEB_PORT" > /app/virtual_env/.browser_port

echo "✅ 웹 서버가 시작되었습니다!"
echo ""

# Flask 프로세스가 종료될 때까지 대기
wait $FLASK_PID
