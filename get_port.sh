#!/bin/bash

# Docker 컨테이너의 실제 포트 번호를 찾아서 브라우저를 자동으로 열어주는 스크립트

CONTAINER_NAME="aws-connect-contact-tracer"
MAX_WAIT=30  # 최대 30초 대기

# 컨테이너가 실행 중인지 확인
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "❌ 컨테이너 '${CONTAINER_NAME}'가 실행 중이 아닙니다."
    echo "먼저 'docker-compose up' 명령으로 컨테이너를 시작하세요."
    exit 1
fi

echo "⏳ 웹 서버가 시작될 때까지 대기 중..."

# 브라우저 포트 파일이 생성될 때까지 대기
WAIT_COUNT=0
while [ $WAIT_COUNT -lt $MAX_WAIT ]; do
    # 컨테이너 내부의 포트 정보 파일 확인
    CONTAINER_PORT=$(docker exec ${CONTAINER_NAME} cat /app/virtual_env/.browser_port 2>/dev/null)

    if [ ! -z "$CONTAINER_PORT" ]; then
        echo "✅ 웹 서버 포트를 찾았습니다: $CONTAINER_PORT"
        break
    fi

    sleep 1
    WAIT_COUNT=$((WAIT_COUNT + 1))
    echo -n "."
done
echo ""

if [ -z "$CONTAINER_PORT" ]; then
    echo "⚠️  포트 정보를 찾는데 시간이 초과되었습니다."
    echo "수동으로 포트를 확인합니다..."
fi

# 매핑된 호스트 포트 찾기
HOST_PORT=$(docker port ${CONTAINER_NAME} 5000 2>/dev/null | sed -n 's/.*0.0.0.0:\([0-9]*\).*/\1/p' | head -1)

if [ -z "$HOST_PORT" ]; then
    # 대체 방법: docker ps로 확인
    HOST_PORT=$(docker ps --filter "name=${CONTAINER_NAME}" --format "{{.Ports}}" | sed -n 's/.*0.0.0.0:\([0-9]*\).*/\1/p' | head -1)
fi

if [ -z "$HOST_PORT" ]; then
    echo "❌ 매핑된 포트를 찾을 수 없습니다."
    echo ""
    echo "수동으로 확인하려면 다음 명령을 실행하세요:"
    echo "  docker ps | grep ${CONTAINER_NAME}"
    exit 1
fi

URL="http://localhost:${HOST_PORT}"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ 웹 서버가 실행 중입니다!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "   접속 URL: ${URL}"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# macOS에서 브라우저 자동 열기
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "🌐 기본 브라우저를 여는 중..."
    open "${URL}"
    echo "✅ 브라우저가 열렸습니다!"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    # Linux
    if command -v xdg-open &> /dev/null; then
        echo "🌐 기본 브라우저를 여는 중..."
        xdg-open "${URL}"
        echo "✅ 브라우저가 열렸습니다!"
    else
        echo "브라우저를 수동으로 여세요: ${URL}"
    fi
else
    echo "브라우저를 수동으로 여세요: ${URL}"
fi
