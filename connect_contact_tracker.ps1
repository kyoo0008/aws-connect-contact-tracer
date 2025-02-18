# PowerShell 스크립트 실행을 위해 관리자 권한으로 실행해야 할 수도 있음.
# 실행 정책 변경 (한 번만 실행)
# Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 가상 환경 및 AWS SSO, Amazon Connect 설정을 수행하는 PowerShell 스크립트
# Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
# ./script.ps1


$VENV_DIR = "virtual_env"
$PYTHON_SCRIPT_FILE = "main.py"
$REQUIREMENTS_FILE = "requirements.txt"

# AWS 계정 매핑
$AWS_ACCOUNTS = @{
    "590183945142" = @{ Alias = "kal-servicecenter"; Region = "ap-northeast-2"; Env = "prd" }
    "637423289860" = @{ Alias = "kal-servicecenter-dev"; Region = "ap-northeast-2"; Env = "dev" }
    "637423576272" = @{ Alias = "kal-servicecenter-stg"; Region = "ap-northeast-2"; Env = "stg" }
}

# PowerShell에서 오류 발생 시 중단
$ErrorActionPreference = "Stop"

# 🛠 가상환경 설정
if (!(Test-Path $VENV_DIR)) {
    python -m venv $VENV_DIR
    Write-Host "`n`"$VENV_DIR`" Python 가상환경이 생성되었습니다.`n"
}

# 가상환경 활성화
$ActivateScript = "$VENV_DIR\Scripts\Activate.ps1"
if (Test-Path $ActivateScript) {
    & $ActivateScript
} else {
    Write-Host "❌ 가상환경을 활성화할 수 없습니다."
    exit 1
}

# 패키지 설치
if (Test-Path $REQUIREMENTS_FILE) {
    pip install -r $REQUIREMENTS_FILE
} else {
    Write-Host "❌ $REQUIREMENTS_FILE 파일이 존재하지 않습니다."
    exit 1
}

# 🛠 GTK+3 설치 확인
$gtk_installed = choco list -l | Select-String "gtk+3"
if (-not $gtk_installed) {
    Write-Host "gtk+3이 설치되어 있지 않습니다. 설치를 시작합니다."
    choco install gtk+3 -y
}

# 🛠 AWS SSO 로그인 확인
try {
    aws sts get-caller-identity | Out-Null
    Write-Host "✅ AWS SSO에 로그인되어 있습니다."
} catch {
    Write-Host "❌ AWS SSO에 로그인되어 있지 않습니다!"
    exit 1
}

# 🛠 AWS 계정 ID 가져오기
$account_id = aws sts get-caller-identity --query "Account" --output text
if ($AWS_ACCOUNTS.ContainsKey($account_id)) {
    $instance_alias = $AWS_ACCOUNTS[$account_id].Alias
    $region = $AWS_ACCOUNTS[$account_id].Region
    $env = $AWS_ACCOUNTS[$account_id].Env
    Write-Host "✅ AWS 환경: $env / 계정: $instance_alias / 리전: $region"
} else {
    Write-Host "❌ 지원되지 않는 AWS 계정 ID: $account_id"
    exit 1
}

# Amazon Connect 인스턴스 ID 가져오기
$instance_id = aws connect list-instances --query "InstanceSummaryList[?InstanceAlias=='$instance_alias'].Id" --output text
if (-not $instance_id) {
    Write-Host "❌ 입력한 Alias의 Amazon Connect 인스턴스를 찾을 수 없습니다!"
    exit 1
} else {
    Write-Host "✅ '$instance_alias'의 인스턴스 ID는 '$instance_id' 입니다."
}

# 🛠 검색 기준 선택
$search_option = @("ContactId", "Customer", "Agent", "History") | Out-GridView -Title "검색 기준 선택" -PassThru
if (-not $search_option) {
    Write-Host "❌ 검색 기준이 선택되지 않았습니다."
    exit 1
}

# 🛠 Contact ID 입력 또는 조회
if ($search_option -eq "ContactId") {
    $selected_contact_id = Read-Host "Amazon Connect Contact Id를 입력하세요 (UUID)"
} elseif ($search_option -eq "History") {
    Write-Host "기록된 Contact Flow 목록을 불러옵니다..."
    $contact_ids = Get-ChildItem "$VENV_DIR" -Filter "$env-main_flow_*.dot" | ForEach-Object {
        $_.Name -replace "^$env-main_flow_", "" -replace ".dot$", ""
    }
    if ($contact_ids.Count -eq 0) {
        Write-Host "❌ 저장된 Contact Flow 기록이 없습니다."
        exit 1
    }
    $selected_contact_id = $contact_ids | Out-GridView -Title "기록된 Contact 선택" -PassThru
} else {
    $selected_contact_id = Read-Host "검색할 ID 또는 정보 입력"
}

if (-not $selected_contact_id) {
    Write-Host "❌ Contact ID가 선택되지 않았습니다."
    exit 1
}

Write-Host "✅ 선택된 Contact ID: $selected_contact_id"

# 🛠 Contact 상세 정보 가져오기
$describe_contact = aws connect describe-contact --contact-id "$selected_contact_id" --instance-id "$instance_id" | ConvertFrom-Json
$contact_attributes = aws connect get-contact-attributes --initial-contact-id "$selected_contact_id" --instance-id "$instance_id" | ConvertFrom-Json

# 🛠 Contact 정보 출력
Write-Host "`n📞 **Contact Details**"
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host "🆔 Contact Id: $selected_contact_id"
Write-Host "📡 Channel: $($describe_contact.Contact.Channel)"
Write-Host "☎️ Customer Phone Number: $($contact_attributes.Attributes.Customer_Number)"
Write-Host "🏢 Center: $($contact_attributes.Attributes.FromCenter)"
Write-Host "📋 Queue: $($contact_attributes.Attributes.Queue_Name)"
Write-Host "👤 Agent: $($contact_attributes.Attributes.Connected_Agent_User_Name)"
Write-Host "🕒 Initiation Timestamp: $($describe_contact.Contact.InitiationTimestamp)"
Write-Host "🕒 Disconnect Timestamp: $($describe_contact.Contact.DisconnectTimestamp)"
Write-Host "📵 Disconnect Reason: $($describe_contact.Contact.DisconnectReason)"
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 🛠 Python 스크립트 실행
Write-Host "`nPython 스크립트를 실행합니다..."
python $PYTHON_SCRIPT_FILE "$instance_alias" "$instance_id" "$selected_contact_id" "$region" "$env"

# 🛠 가상환경 비활성화
deactivate

