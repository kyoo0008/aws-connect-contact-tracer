#!/bin/bash -e

# Copyright © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms and the SOW between the parties dated [March 18, 2024].

###
# 현재 Git Repo의 Flow JSON 파일을 Local에서 분석하여, Terraform HCL 코드로 IaC 작성에 도움을 줄 수 있는 예시코드를 생성하는 Python 스크립트를 실행
#

VENV_DIR="virtual_env"
# PYTHON_SCRIPT_FILE="test.py"
PYTHON_SCRIPT_FILE="main.py"
REQUIREMENTS_FILE="requirements.txt"
EMAIL_REGEX="^[a-zA-Z0-9!#\$%&'*+/=?^_\`{|}~-]+(\.[a-zA-Z0-9!#$%&'*+/=?^_\`{|}~-]+)*@([a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?\$"
UUID_REGEX=^\{?[A-F0-9a-f]{8}-[A-F0-9a-f]{4}-[A-F0-9a-f]{4}-[A-F0-9a-f]{4}-[A-F0-9a-f]{12}\}?$
HANGUL_NAME_REGEX="^[가-힣]{2,}[a-zA-Z]?$"  # 한글 두 글자 이상 + 선택적 영문자
cols_num=5


# Insights 쿼리
QUERY='fields @timestamp, @message, @logStream, @log
| filter @message like "\"level\":\"ERROR\""
| sort @timestamp desc
| limit 10000'

if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
  echo -e "\n\"$VENV_DIR\" Python 가상환경이 생성되었습니다.\n"

  # 가상환경 활성화
  source "$VENV_DIR/bin/activate"

  if [ -f "$REQUIREMENTS_FILE" ]; then
    pip install -r $REQUIREMENTS_FILE
  else
    echo "$REQUIREMENTS_FILE 파일이 존재하지 않습니다."
    exit 1
  fi
else
  # 가상환경 활성화
  source "$VENV_DIR/bin/activate"
fi



# tkinter 설치
if ! brew list gtk+3 &> /dev/null; then
    echo "gtk+3이 설치되어 있지 않습니다. 설치를 시작합니다."
    brew install gtk+3
    if [ $? -eq 0 ]; then
        echo "gtk+3이 성공적으로 설치되었습니다."
    else
        echo "gtk+3 설치에 실패했습니다. 에러 로그를 확인하세요."
        exit 1
    fi
fi

# AWS SSO 로그인 상태 확인
aws sts get-caller-identity >/dev/null 2>&1
if [ $? -eq 0 ]; then
  echo "✅ AWS SSO에 로그인되어 있습니다."
else
  echo "❌ AWS SSO에 로그인되어 있지 않습니다!"
  exit 1
fi

# AWS 계정 ID 확인 및 instance alias 자동 설정
account_id=$(aws sts get-caller-identity --query "Account" --output text)
case "$account_id" in
  "590183945142") instance_alias="kal-servicecenter" && region="ap-northeast-2" && env=prd;;
  "637423289860") instance_alias="kal-servicecenter-dev" && region="ap-northeast-2" && env=dev;;
  "637423576272") instance_alias="kal-servicecenter-stg" && region="ap-northeast-2" && env=stg;;
  # "009160043124") instance_alias="hist-aicc-test-1" && region="us-east-1";;
  *) echo "❌ 지원되지 않는 AWS 계정 ID: $account_id" && exit 1 ;;
esac

get_instance_id_from_alias() {
  local instance_alias="$1"
  local instance_id=$(aws connect list-instances --query "InstanceSummaryList[?InstanceAlias=='$instance_alias'].Id" --output text)
  echo "$instance_id"
}

convert_to_millis() {
  date_str="$1"
  date -j -f "%Y-%m-%dT%H:%M:%S%z" "${date_str}" "+%s000"
}

# History 목록 생성 함수
list_history_files() {
    find "$VENV_DIR" -type f -name "$env-main_flow_*.dot" | while read -r file; do
        filename=$(basename "$file")
        contact_id=${filename#$env-main_flow_}
        contact_id=${contact_id%.dot}
        created_time=$(stat -f "%Sm" -t "%Y-%m-%d %H:%M:%S" "$file")  # macOS용
        echo "$contact_id $created_time"
    done
}

list_contact_flow_lambda_error_list() {
  # 로그 그룹 배열 (필요한 로그 그룹을 추가하세요)
  LOG_GROUPS=(
      "/aws/lmd/aicc-connect-flow-base/flow-agent-workspace-handler"
      "/aws/lmd/aicc-connect-flow-base/flow-alms-if"
      "/aws/lmd/aicc-connect-flow-base/flow-chat-app"
      "/aws/lmd/aicc-connect-flow-base/flow-idnv-async-if"
      "/aws/lmd/aicc-connect-flow-base/flow-idnv-common-if"
      "/aws/lmd/aicc-connect-flow-base/flow-internal-handler"
      "/aws/lmd/aicc-connect-flow-base/flow-kalis-if"
      "/aws/lmd/aicc-connect-flow-base/flow-mdm-if"
      "/aws/lmd/aicc-connect-flow-base/flow-ods-if"
      "/aws/lmd/aicc-connect-flow-base/flow-oneid-if"
      "/aws/lmd/aicc-connect-flow-base/flow-sample-integration"
      "/aws/lmd/aicc-connect-flow-base/flow-tms-if"
      "/aws/lmd/aicc-connect-flow-base/flow-vars-controller"
  )

  # Insights 쿼리
  QUERY="fields @timestamp, @message, @logStream, @log
  | filter @message like '\"level\":\"ERROR\"'
  | sort @timestamp desc
  | limit 10000"


  # 실행 결과를 저장할 변수
  RESULTS=""

  # 각 로그 그룹에 대해 쿼리 실행
  for LOG_GROUP in "${LOG_GROUPS[@]}"; do

      QUERY_ID=$(aws logs start-query --log-group-name "$LOG_GROUP" --query-string "$QUERY" --start-time $(date -v-48H "+%s000") --end-time $(date "+%s000") --region ap-northeast-2 --query 'queryId' --output text)
      
      # 쿼리 실행 후 대기 (CloudWatch는 쿼리가 실행되는 데 시간이 필요함)
      while true; do
          STATUS=$(aws logs get-query-results --query-id "$QUERY_ID" --region ap-northeast-2 --query 'status' --output text)
          if [ "$STATUS" == "Complete" ]; then
              break
          fi
          sleep 2
      done


      # 결과 가져오기
      RESPONSE=$(aws logs get-query-results --query-id "$QUERY_ID" --region ap-northeast-2 --output json)

      # JSON에서 ContactId 추출
      CONTACT_INFO=$(echo "$RESPONSE" | jq -r '
          .results[] | 
          {
              timestamp: (map(select(.field == "@timestamp"))[0].value // empty),
              message: (map(select(.field == "@message"))[0].value | fromjson)
          } |
          select(.message.ContactId) |
          "\(.message.ContactId)\t\(.message.service)\t\(.timestamp)"
      ')

      if [ ! -z "$CONTACT_INFO" ]; then
        echo "$CONTACT_INFO"$'\n'
      fi
  done

  


}

# Amazon Connect Instance ID
instance_id=$(get_instance_id_from_alias "$instance_alias")
if [ -z "$instance_id" ]; then
  echo "❌ 입력한 Alias의 Amazon Connect 인스턴스를 찾을 수 없습니다!"
  exit 1
else
  echo "✅ '$instance_alias'의 인스턴스 ID는 '$instance_id' 입니다."
fi


# echo -e "\n2. Amazon Connect Contact Id를 입력하거나 Enter를 눌러 선택 메뉴를 사용하세요."
# read -r -p "❯ " selected_contact_id

# if [ -z "$selected_contact_id" ]; then


# fzf를 통한 검색 조건 선택


search_option=$(echo -e "ContactId\nCustomer\nAgent\nHistory\nLambdaError" | fzf --height 7 --prompt "검색할 기준을 선택하세요 (History, Agent, Customer, ContactId):" )

case $search_option in
  "History")
    echo "기록된 Contact Flow 목록을 불러옵니다..."
    contact_ids=$(list_history_files)

    if [ -z "$contact_ids" ]; then
      echo "❌ 저장된 Contact Flow 기록이 없습니다."
      exit 1
    fi

    selected_contact_id=$(echo "$contact_ids" | fzf --height 10 --prompt "기록된 Contact 선택" | awk '{print $1}')
    ;;
  "Agent")
    echo -e "Agent ID, 한글이름, 또는 Email을 입력하세요:(e.g., 상담사 uuid, 홍길동B, 또는 이메일 형식의 ID)"
    # echo -e "Agent ID 또는 Email 입력 시 빠르게 검색할 수 있습니다."
    read -r -p "❯ " agent_input
    echo "입력된 Agent 정보: $agent_input"

    if [[ $agent_input =~ $UUID_REGEX ]]; then # uuid
      agent_id=$(aws connect describe-user --instance-id $instance_id --user-id $agent_input | jq -r '.User.Username')
    elif [[ $agent_input =~ $EMAIL_REGEX ]]; then
      agent_id=$agent_input
    elif [[ $agent_input =~ $HANGUL_NAME_REGEX ]]; then  # 한글 Full Name 입력
      echo "🔍 한글 Full Name 검색 중..."

      # 전체 상담사 목록에서 검색
      agent_id=$(aws connect search-users --instance-id $instance_id --output json | \
          jq -r --arg name "$agent_input" '
          .Users[] | select((.IdentityInfo.LastName+.IdentityInfo.FirstName) == $name) | .Username'
      )

      if [[ -z "$agent_id" ]]; then
          echo "❌ 오류: 해당 Full Name을 가진 상담사를 찾을 수 없습니다."
          exit 1
      fi
    else
      echo "❌ 오류: 유효한 Agent ID (UUID) 또는 이메일 형식의 ID를 입력하세요."
      exit 1
    fi

    gsi="gsi2"
    key_name="gsi2Pk"
    key_value="contact#agentUserName#$agent_id"

    ;;
  "Customer")
    echo -e "Customer Profile ID 또는 Phone Number 또는 Skypass Number를 입력하세요(e.g., 32자리 profileId 또는 E.164 포맷 +821012341234 또는 Skypass Number):"
    read -r -p "❯ " customer_info
    echo "입력된 Customer 정보: $customer_info"

      # 입력값 확인
    if [[ "$customer_info" =~ ^[a-zA-Z0-9]{32}$ ]]; then
      gsi="gsi1"
      key_name="gsi1Pk"
      key_value="contact#profileId#$customer_info"
    elif [[ "$customer_info" =~ ^\+[1-9][0-9]{7,14}$ ]]; then
      gsi="gsi3"
      key_name="gsi3Pk"
      key_value="contact#phoneNumber#$customer_info"
    elif [[ "$customer_info" =~ ^[a-zA-Z0-9]{12}$ ]]; then
      gsi="gsi9"
      key_name="gsi9Pk"
      key_value="contact#skypassNumber#$customer_info" 
    else
      echo "❌ 오류: 유효한 Customer Profile ID (32자리) 또는 E.164 형식의 Phone Number 또는 SkyPass Number 12자리를 입력하세요."
      exit 1
    fi

    ;;
  "ContactId")
    echo "Amazon Connect Contact Id를 입력하세요 (uuid):"
    read -r -p "❯ " selected_contact_id
    if [[ $selected_contact_id =~ $UUID_REGEX ]]; then # uuid
      echo "입력된 Contact Id: $selected_contact_id"
    else
      echo "❌ 오류: 유효한 Contact ID (UUID)를 입력하세요."
      exit 1
    fi
    ;;
  "LambdaError")
    echo "⏳ 탐색 중 입니다..."
    contact_ids=$(list_contact_flow_lambda_error_list)
    if [ -z "$contact_ids" ]; then
      echo "❌ 저장된 Contact Flow 기록이 없습니다."
      exit 1
    fi

    selected_contact_id=$(echo "$contact_ids" | fzf --height 10 --prompt "기록된 Contact 선택" | awk '{print $1}')
    ;;
  *)
    echo "올바른 옵션을 선택하세요."
    exit 1
    ;;
esac
if [ -z "$selected_contact_id" ]; then

  # contact id 리스트를 가져올 때 방식에 차이가 있을 수 있음 
  contact_ids=$(
        aws dynamodb query \
          --table-name "aicc-$env-ddb-agent-contact" \
          --index-name "aicc-$env-ddb-agent-contact-$gsi" \
          --key-condition-expression "$key_name = :value" \
          --expression-attribute-values "{\":value\": {\"S\": \"$key_value\"}}" \
          --query "Items[].[contactId.S, channel.S, initiationMethod.S, initiationTimestamp.S, disconnectTimestamp.S]" \
          --no-scan-index-forward \
          --max-items 20 \
          --output json | jq -r 'sort_by(.[3]) | reverse | .[] | select(.[0] != null) | "\(.[0])  \(.[1]) \(.[2])  \(.[3]) ~ \(.[4] // "N/A")"' | head -n 20
        )

  selected_contact_id=$(echo "$contact_ids" | tr '\t' '\n' | fzf --height 20 --prompt "최근 진행한 Contact ID 선택" | awk '{print $1}')
fi

if [ -z "$selected_contact_id" ]; then
  echo "❌ Contact ID가 선택되지 않았습니다."
  exit 1
else
  echo "✅ 선택된 Contact ID: $selected_contact_id"
  # Extract information using AWS CLI
  describe_contact=$(aws connect describe-contact --contact-id "$selected_contact_id" --instance-id "$instance_id")
  contact_attributes=$(aws connect get-contact-attributes --initial-contact-id $selected_contact_id --instance-id $instance_id )
  associated_contacts=$(aws connect list-associated-contacts --instance-id $instance_id --contact-id $selected_contact_id)

  # AICC Info
  center=$(echo $contact_attributes | jq -r '.Attributes.FromCenter')
  if [[ -z "$center" || "$center" == "null" ]]; then
    center=$(echo "$describe_contact" | jq -r '.Attributes.AgentCenter')
  fi
  if [[ -z "$center" || "$center" == "null" ]]; then
    center=$(echo "$describe_contact" | jq -r '.Contact.Tags.FromCenter')
  fi
  service_number=$(echo "$contact_attributes" | jq -r '.Attributes.Service_Number')
  customer_number=$(echo "$contact_attributes" | jq -r '.Attributes.Customer_Number')
  customer_skypass_number=$(echo "$contact_attributes" | jq -r '.Attributes.Agent_Customer_Skypass')
  customer_profile_id=$(echo "$contact_attributes" | jq -r '.Attributes.Matched_Profile_Id')
  contact_direction=$(echo "$contact_attributes" | jq -r '.Attributes.Contact_Direction')
  one_id_number=$(echo "$contact_attributes" | jq -r '.Attributes.One_Id_Number')

  # Common Contact Center Info
  channel=$(echo "$describe_contact" | jq -r '.Contact.Channel')
  agent_info=$(echo "$describe_contact" | jq '.Contact.AgentInfo')
  
  initiation_timestamp=$(echo "$describe_contact" | jq -r '.Contact.InitiationTimestamp')
  disconnect_timestamp=$(echo "$describe_contact" | jq -r '.Contact.DisconnectTimestamp')
  last_update_timestamp=$(echo "$describe_contact" | jq -r '.Contact.LastUpdateTimestamp')

  disconnect_reason=$(echo "$describe_contact" | jq -r '.Contact.DisconnectReason')

  # Fetch queue name
  # queue_name=$(aws connect describe-queue --instance-id $instance_id --queue-id "$queue_id" | jq -r '.Name')
  queue_name=$(echo $contact_attributes | jq -r '.Attributes.Queue_Name')
  queue_id=$(echo $describe_contact | jq -r '.Contact.QueueInfo.Id')
  enqueue_timestamp=$(echo $describe_contact | jq -r '.Contact.QueueInfo.EnqueueTimestamp')
  # Fetch agent details
  # agent_name=$(echo "$agent_info" | jq -r '.Name')
  agent_name=$(echo $contact_attributes | jq -r '.Attributes.Connected_Agent_User_Name')
  agent_id=$(echo "$describe_contact" | jq -r '.Contact.AgentInfo.Id')
  agent_device_info=$(echo "$describe_contact" | jq -r '.Contact.AgentInfo.DeviceInfo')
  agent_device_info_text=$(echo "$describe_contact" | jq -r '.Contact.AgentInfo.DeviceInfo | "\(.OperatingSystem) / \(.PlatformName) ver.\(.PlatformVersion) "')

  connected_timestamp=$(echo "$describe_contact" | jq -r '.Contact.AgentInfo.ConnectedToAgentTimestamp')
  contact_flow=$(echo $contact_attributes | jq -r '.Attributes.ContactFlow')
  quality_metrics_score=$(echo $describe_contact | jq -r '.Contact.QualityMetrics.Agent.Audio.QualityScore')
  potential_quality_issues=$(echo $describe_contact | jq -r '.Contact.QualityMetrics.Agent.Audio.PotentialQualityIssues')
  disconnect_reason=$(echo "$describe_contact" | jq -r '.Contact.DisconnectReason')

  echo -e "Contact Attributes : \n$(echo $contact_attributes  | jq .)"

  echo -e "List Associated Contacts : \n$(echo $associated_contacts | jq .)"

  echo -e "Describe Contact : \n$(echo $describe_contact  | jq .)"


  GREEN="\033[32m"
  RESET="\033[0m"
  BORDER="${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

  # Calculate max width for alignment
  MAX_WIDTH=66  # 조정 가능

  # Function to format output with padding
  format_line() {
      local text="$1"
      local padding=$((MAX_WIDTH - ${#text}))
      printf " %s %*s \n" "$text" "$padding" ""
  }
  # Display Contact Details with Borders
  echo -e "\n$BORDER"
  format_line "📞 **Contact Details**"
  echo -e "$BORDER"
 

  if [[ ! -z "$selected_contact_id" && $selected_contact_id != "null" ]]; then
    format_line "🆔 Contact Id: $selected_contact_id"
  fi
    
  if [[ ! -z "$channel" && $channel != "null" ]]; then
    format_line "📡 Channel: $channel ( $contact_direction )"
  fi
    
  if [[ ! -z "$service_number" && $service_number != "null" ]]; then
    format_line "📟 Service Number: $service_number"
  fi
    
  if [[ ! -z "$customer_number" && $customer_number != "null" ]]; then
    format_line "☎️ Customer Phone Number: $customer_number"
  fi
    
  if [[ ! -z "$customer_skypass_number" && $customer_skypass_number != "null" ]]; then
    format_line "✈️ Customer Skypass Number: $customer_skypass_number"
  fi
    
  if [[ ! -z "$customer_profile_id" && $customer_profile_id != "null" ]]; then
    format_line "👤 Customer Profile Id: $customer_profile_id"
  fi
    
  if [[ ! -z "$center" && $center != "null" ]]; then
    format_line "🏢 Center: $center"
  fi
    
  if [[ ! -z "$queue_id" && $queue_id != "null" ]]; then
    format_line "📋 Queue: $queue_name"
    format_line "          (ID: $queue_id)"
  fi
    
  if [[ ! -z "$agent_id" && $agent_id != "null" ]]; then
    format_line "👤 Agent: $agent_name "
    if [[ ! -z "$agent_device_info" && $agent_device_info != "null" ]]; then
      format_line "        (Device Info : $agent_device_info_text)"
    fi
    format_line "          (ID: $agent_id)"
  fi

  if [[ ! -z "$enqueue_timestamp" && $enqueue_timestamp != "null" ]]; then
    format_line "🕒 Enqueue Timestamp: $enqueue_timestamp"
  fi
    
  if [[ ! -z "$connected_timestamp" && $connected_timestamp != "null" ]]; then
    format_line "🕒 Connected Agent Timestamp: $connected_timestamp"
  fi
    
  if [[ ! -z "$initiation_timestamp" && $initiation_timestamp != "null" ]]; then
    format_line "🕒 Initiation Timestamp: $initiation_timestamp"
  fi
    
  if [[ ! -z "$disconnect_timestamp" && $disconnect_timestamp != "null" ]]; then
    format_line "🕒 Disconnect Timestamp: $disconnect_timestamp"
  fi
    
  if [[ ! -z "$last_update_timestamp" && $last_update_timestamp != "null" ]]; then
    format_line "🕒 Last Update Timestamp: $last_update_timestamp"
  fi
    
  if [[ ! -z "$contact_flow" && $contact_flow != "null" ]]; then
    format_line "🔀 Flow: $contact_flow"
  fi

  if [[ $channel == "VOICE" && $contact_direction == "INBOUND" && $quality_metrics_score != "null" && ! -z $quality_metrics_score ]]; then
      format_line "💯 Quality Score : $quality_metrics_score"
      format_line "❗️ Potential Quality Issues : $potential_quality_issues"
      # format_line "❓ Disconnect Reason: $disconnect_reason"
  fi

  if [[ ! -z "$disconnect_reason" && $disconnect_reason != "null" ]]; then
    format_line "📵 Disconnect Reason: $disconnect_reason"
  fi
  echo -e "$BORDER"
fi
 


# Python 스크립트 실행
echo -e '\n'
python $PYTHON_SCRIPT_FILE "$instance_alias" "$instance_id" "$selected_contact_id" "$region" "$initiation_timestamp" "$associated_contacts" "$search_option" "$env"

# 가상환경 비활성화
deactivate