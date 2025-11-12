# aws-connect-contact-tracer
Contact ID 입력 후 CloudWatch log를 그래프로 구현하는 Utility 입니다.

![image](https://github.com/user-attachments/assets/d60d9320-46d7-4767-8e99-17946e8dca47)

![image](https://github.com/user-attachments/assets/3ceeae26-d8ed-46c7-be05-f8be5035f6dd)

# 사용 방법

## 1. Docker를 사용한 실행 (권장)

### 사전 요구사항
- Docker 및 Docker Compose 설치
- 호스트 OS에서 AWS SSO 로그인 완료

```bash
# AWS SSO 로그인 (호스트에서 실행)
aws sso login --profile <your-profile>

# AWS Profile 환경 변수 설정
export AWS_PROFILE=<your-profile>
```

### Docker로 실행

```bash
# Docker 이미지 빌드 및 실행
docker-compose up --build

# 또는 직접 Docker 명령어 사용
docker build -t connect-tracer .
docker run -it --rm \
  -v ~/.aws:/root/.aws:ro \
  -v $(pwd)/virtual_env:/app/virtual_env \
  -e AWS_PROFILE=$AWS_PROFILE \
  connect-tracer \
  ./connect_contact_tracker.sh
```

### 주요 볼륨 마운트
- `~/.aws:/root/.aws:ro` - AWS 자격 증명 및 SSO 세션 정보 (읽기 전용)
- `./virtual_env:/app/virtual_env` - 생성된 그래프 파일 저장

## 2. 로컬 환경에서 직접 실행

### 사전 요구사항 (macOS)
- Python 3.x
- Homebrew
- AWS CLI v2
- gtk+3 (스크립트에서 자동 설치)

### 실행

```bash
# 스크립트 실행 권한 부여
chmod +x connect_contact_tracker.sh

# 스크립트 실행
./connect_contact_tracker.sh
```

## 환경 변수

- `AWS_PROFILE` - 사용할 AWS Profile 이름
- `AWS_REGION` - AWS 리전 (기본값: ap-northeast-2)
- `AWS_DEFAULT_REGION` - AWS 기본 리전

## 검색 옵션

스크립트 실행 시 다음 검색 옵션을 선택할 수 있습니다:

- **ContactId** - Contact ID 직접 입력
- **Customer** - Customer Profile ID, 전화번호 또는 Skypass 번호로 검색
- **Agent** - Agent ID, 이름 또는 이메일로 검색
- **History** - 이전에 조회한 Contact 목록
- **LambdaError** - Lambda 에러 또는 Timeout 발생 Contact 검색
- **ContactFlow** - Contact Flow 이름으로 검색
- **DNIS** - DNIS 번호로 검색

# Links

* 사용 매뉴얼 : https://pricey-mollusk-313.notion.site/AWS-Connect-Contact-Tracker-19066f5d694480ec9e43d9fb6b631be1?pvs=73
* xdot.ui : https://github.com/jrfonseca/xdot.py
* Graphviz : https://www.graphviz.org/
* PyGObject : https://pygobject.gnome.org/
