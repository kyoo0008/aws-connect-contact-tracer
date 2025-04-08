import re
import sys
import json
import time
import pytz
import boto3
import os
import subprocess

from datetime import datetime, timedelta
from collections import defaultdict

from describe_flow import get_contact_flow, \
                        get_contact_flow_module

from decompress_datadog_gzip import decompress_datadog_logs

# 그래프에서 한 줄에 표시할 노드 수 
COLS_NUM = 5

# 그래프에서 제외할 Flow Name
EXCEPT_CONTACT_FLOW_NAME = [
    '99_MOD_Dummy', 'InvokeFlowModule'
]
def check_json_file_exists(directory):
    try:
        for filename in os.listdir(directory):
            if filename.endswith('.json'):
                return True
        return False
    except Exception:
        return False

# Util
def generate_node_ids(logs,sort=True):
    if sort:
        logs.sort(key=lambda log: log['Timestamp'])  # timestamp 기준 정렬
    flow_indices = defaultdict(int)
    last_flow_name = None  # 마지막 유효한 Entry 노드의 flow_name 저장
    last_node_id = None  # 마지막 Entry 기반 node_id 저장

    for log in logs:
        flow_name = log['ContactFlowName']

        if last_flow_name and ("MOD_" in flow_name or flow_name == last_flow_name):
            # MOD_ 또는 이전과 동일한 Entry라면 같은 node_id 유지
            log['node_id'] = last_node_id
        else:
            # 새로운 Entry 노드가 등장하면 새로운 node_id 할당
            flow_indices[flow_name] += 1
            last_node_id = f"{flow_name}_{flow_indices[flow_name]}"
            log['node_id'] = last_node_id
            last_flow_name = flow_name  # 새로운 Entry로 업데이트

    return logs

def valid_uuid(uuid):
    if not uuid:
        return False
    regex = re.compile('^[a-f0-9]{8}-?[a-f0-9]{4}-?4[a-f0-9]{3}-?[89ab][a-f0-9]{3}-?[a-f0-9]{12}', re.I)
    match = regex.match(uuid)
    return bool(match)

def sanitize_label(label):
    """
    DOT 그래프에서 사용할 수 없는 제어 문자 제거.
    """
    if not label:
        return ""
        
    label = label.replace('&','n')
    # 유효하지 않은 ASCII 제어 문자 제거 (0x00~0x1F 및 0x7F)
    return re.sub(r'[\x00-\x1F\x7F]', '', label)

def fetch_logs(contact_id, initiation_timestamp, region, log_group, env, instance_id):
    cloudwatch_client = boto3.client("logs", region_name=region)

    """
    CloudWatch Logs에서 ContactId에 해당하는 로그를 가져옵니다.
    """
    query = f"""
        fields @timestamp, @message
        | filter ContactId = \"{contact_id}\"
        | sort @timestamp asc
        """

    initiation_time = datetime.fromisoformat(initiation_timestamp).astimezone(pytz.UTC)

    # Start Time
    start_time = initiation_time - timedelta(hours=12)

    # End Time
    end_time = initiation_time + timedelta(hours=12)

    # Lambda Log Group
    lambda_log_groups = set()

    try:
        start_query_response = cloudwatch_client.start_query(
            logGroupName=log_group,
            startTime=int(start_time.timestamp()),  # UTC 기준 -12시간
            endTime=int(end_time.timestamp()),  # UTC 기준 +12시간
            queryString=query,
        )
    except Exception as e:
        if "MalformedQueryException" in str(e) :
            print("1일 전부터 발생한 ContactId 입력 후 현재 Cloudwatch에서 조회 가능합니다. ")
            print("S3에 백업 된 데이터를 불러옵니다...S3에서 가져온 데이터는 Lambda Xray Trace기능이 없습니다.(추후 개발 예정)")
            print(f"contact id : {contact_id}")
            datadog_logs, _ = decompress_datadog_logs(env,contact_id,instance_id,region)
            datadog_logs = generate_node_ids(datadog_logs, False)
            result_logs = []
            contact_flow_ids = set()

            for json_value in datadog_logs:
                
                # 제외 contact flow 건너뛰기 
                if json_value.get("ContactFlowName") not in EXCEPT_CONTACT_FLOW_NAME and json_value.get("ContactId") == contact_id: 
                    result_logs.append(json_value)
                    contact_flow_ids.add(json_value.get("ContactFlowId"))

            for contact_flow_id in contact_flow_ids:
                if 'contact-flow' in contact_flow_id:
                    jsonfile_name = f"./virtual_env/describe_contact_flow_{contact_flow_id}.json"

                    if not os.path.isfile(jsonfile_name):
                        get_contact_flow(contact_flow_id, region)
                elif 'flow-module' in contact_flow_id:
                    jsonfile_name = f"./virtual_env/describe_flow_module_{contact_flow_id}.json"

                    if not os.path.isfile(jsonfile_name):
                        get_contact_flow_module(contact_flow_id, region)

            return datadog_logs, []
        else:
            print(f"Error : {e}")
        sys.exit(1)

    query_id = start_query_response["queryId"]

    # 쿼리 결과 기다리기
    response = None
    while response is None or response["status"] == "Running":
        time.sleep(1)
        response = cloudwatch_client.get_query_results(queryId=query_id)


    logs = []
    contact_flow_ids = set()
    for result in response["results"]:
        for field in result:
            if field["field"] == "@message":
                json_value = json.loads(sanitize_label(field["value"]))
                
                # 제외 contact flow 건너뛰기 
                if json_value.get("ContactFlowName") not in EXCEPT_CONTACT_FLOW_NAME: 
                    logs.append(json_value)
                    contact_flow_ids.add(json_value.get("ContactFlowId"))

                if "BotAliasArn" in str(json_value):
                    
                    lambda_log_groups.add(f"/aws/lex/aicc/{get_bot_name_from_alias_arn(json_value.get("Parameters")["BotAliasArn"])}")


                # Lambda 함수 수집 
                if json_value.get("ContactFlowModuleType") == "InvokeExternalResource":
                    function_arn = json_value.get("Parameters")["FunctionArn"]
                    params = json_value.get("Parameters")["Parameters"]


                    lambda_log_groups.add(get_lambda_log_groups_from_arn(function_arn))
                    if "idnv-common-if" in function_arn: # common-if 예외처리 
                        lambda_log_groups.add(get_lambda_log_groups_from_arn(function_arn.replace("common-if","async-if")))

                    if params.get("keywords") and "chat" == params.get("keywords"):
                        lambda_log_groups.add("/aws/lmd/aicc-chat-app/alb-chat-if")


    # JSON 파일 저장    
    output_json_path = f"./virtual_env/contact_flow_{contact_id}.json"
    with open(output_json_path, "w", encoding="utf-8") as json_file:
        json.dump(logs, json_file, ensure_ascii=False, indent=4)

    print(f"JSON 파일이 저장되었습니다: {output_json_path}")

    logs = generate_node_ids(logs)

    for contact_flow_id in contact_flow_ids:
        if 'contact-flow' in contact_flow_id:
            jsonfile_name = f"./virtual_env/describe_contact_flow_{contact_flow_id}.json"

            if not os.path.isfile(jsonfile_name):
                get_contact_flow(contact_flow_id, region)
        elif 'flow-module' in contact_flow_id:
            jsonfile_name = f"./virtual_env/describe_flow_module_{contact_flow_id}.json"

            if not os.path.isfile(jsonfile_name):
                get_contact_flow_module(contact_flow_id, region)

    lambda_logs = {}

    for lambda_log_group in lambda_log_groups:
        function_name = lambda_log_group.split("/")[4]
        lambda_logs[function_name] = fetch_lambda_logs(contact_id, initiation_timestamp, region, lambda_log_group)

    try:
        if len(lambda_logs['flow-idnv-async-if']) > 0:
            lambda_logs['flow-idnv-common-if'] += lambda_logs['flow-idnv-async-if']
    except Exception as e:
        print('')

    return logs, lambda_logs, contact_flow_ids

# flow-internal-handler
def get_func_name(arn):
    return "-".join(arn.split(":")[6].split("-")[3:])

def get_lambda_log_groups_from_arn(arn):
    return "/aws/lmd/aicc-connect-flow-base/"+get_func_name(arn)

def fetch_lambda_logs(contact_id, initiation_timestamp, region, log_group):

    cloudwatch_client = boto3.client("logs", region_name=region)

    """
    CloudWatch Logs에서 ContactId에 해당하는 로그를 가져옵니다.
    """

    if "bot" not in log_group:
        query = f"""
            fields @timestamp, @message
            | filter ContactId = \"{contact_id}\"
            | sort @timestamp asc
            """
    else:
        query = f"""
            fields @timestamp, @message
            | filter @message like \"{contact_id}\"
            | sort @timestamp asc
            """

    initiation_time = datetime.fromisoformat(initiation_timestamp).astimezone(pytz.UTC)

    # Start Time
    start_time = initiation_time - timedelta(hours=12)

    # End Time
    end_time = initiation_time + timedelta(hours=12)

    try:
        start_query_response = cloudwatch_client.start_query(
            logGroupName=log_group,
            startTime=int(start_time.timestamp()),  # UTC 기준 -12시간
            endTime=int(end_time.timestamp()),  # UTC 기준 +12시간
            queryString=query,
        )
    except Exception as e:
        if "MalformedQueryException" in str(e) :
            print(f"Error : {e}, 1일 전부터 발생한 ContactId 입력 후 조회 가능합니다.")
        else:
            print(f"Error : {e}")
        sys.exit(1)

    query_id = start_query_response["queryId"]

    # 쿼리 결과 기다리기
    response = None
    while response is None or response["status"] == "Running":
        time.sleep(1)
        response = cloudwatch_client.get_query_results(queryId=query_id)


    logs = filter_lambda_logs(response)

    # To-do : delete
    if "bot" in log_group:
        # JSON 파일 저장    
        output_json_path = f"./virtual_env/lmd_{contact_id + "_".join(log_group.split("/"))}.json"
        with open(output_json_path, "w", encoding="utf-8") as json_file:
            json.dump(logs, json_file, ensure_ascii=False, indent=4)

    return logs

def filter_lambda_logs(response):
    logs = []
    if len(response["results"]) > 0:
        for result in response["results"]:
            for field in result:
                if field["field"] == "@message":
                    json_value = json.loads(field["value"])
                    logs.append(json_value)

    return logs

def get_xray_trace(trace_id, region):
    # AWS CLI 명령어 실행
    cmd = [
        "aws", "xray", "batch-get-traces",
        "--trace-ids", trace_id,
        "--region", region,
        "--output", "json"
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        return {"error": "Failed to retrieve trace", "details": result.stderr}

    # JSON 파싱
    try:
        data = json.loads(result.stdout)

        with open(f"./virtual_env/batch_xray_{trace_id}.json","w",encoding="utf-8") as f:
            # json.loads(segment["Document"])
            traces = [
                json.loads(segment["Document"])
                for trace in data.get("Traces", [])
                for segment in trace.get("Segments", [])
                # if "Document" in segment and "DynamoDB" in json.loads(segment["Document"]).get("name", "")
            ]
            json.dump(traces, f, indent=2, ensure_ascii=False)
            f.close()

        
        traces = [
            json.loads(segment["Document"])
            for trace in data.get("Traces", [])
            for segment in trace.get("Segments", [])
            if "Document" in segment 
            # and "DynamoDB" in json.loads(segment["Document"]).get("name", "")
        ]
        return traces
    except json.JSONDecodeError:
        return {"error": "Invalid JSON response from AWS CLI"}


def wrap_text(text, is_just_cut=False, max_length=72, wrap_at=25):
    """
    - 25자마다 줄바꿈
    - 최대 72자(3줄)까지만 표시하고 초과하면 "..." 추가
    """
    if not text:
        return ""

    # 25자 단위로 줄바꿈 추가
    if not is_just_cut:
        wrapped_text = "\n".join([text[i:i+wrap_at] for i in range(0, len(text), wrap_at)])

        # 초과 시 "..." 추가
        if len(text) > max_length:
            wrapped_text = "\n".join([text[i:i+wrap_at] for i in range(0, max_length, wrap_at)]) + "..."
    else:
        if len(text) > max_length:
            wrapped_text = text[:max_length] + "..."
        else:
            wrapped_text = text
    return wrapped_text

def wrap_transcript(text):
    # 띄어쓰기를 기준으로 단어 단위로 split
    words = text.split()

    # 결과 저장할 배열
    text_arr = []
    current_text = ""

    for word in words:
        # 현재 문자열에 추가했을 때 20자를 넘는지 확인
        if len(current_text) + len(word) + (1 if current_text else 0) > 20:
            text_arr.append(current_text)  # 현재 문자열을 배열에 추가
            current_text = word  # 새 문자열 시작
        else:
            current_text += (" " if current_text else "") + word  # 단어 추가

    # 마지막 남은 문자열 추가
    if current_text:
        text_arr.append(current_text)

    return "\n".join(text_arr)


def check_kor(text):
    p = re.compile('[ㄱ-힣]')
    r = p.search(text)
    if r is None:
        return False
    else:
        return True

def apply_rank(dot, nodes):
    """Graphviz의 rank 속성 적용 (홀수 줄은 순차, 짝수 줄은 역순)"""
    # rank 설정 - 홀수 줄 순차, 짝수 줄 역순으로 세로로 묶음
    num_logs = len(nodes)
    cols = COLS_NUM  # 한 줄에 표시할 노드 개수 
    rows = (num_logs + cols - 1) // cols  # 줄 수 계산

    for col in range(cols):
        group = []
        for row in range(rows):
            idx = row * cols + col
            if row % 2 == 1:  # 짝수 줄은 역순
                idx = (row + 1) * cols - col - 1
            if idx < num_logs:
                group.append(f'"{nodes[idx]}"')
        if group:
            dot.body.append('\n{rank=same; ' + ' '.join(group) + '}\n')

def replace_generic_arn(log):
    """
    딕셔너리 형태의 log에서 모든 ARN을 찾아 '/{key}/UUID' 부분을 '***{key} ARN***'으로 변경하는 함수
    """
    # 정규식 패턴: arn:aws:<service>:<region>:<account>:instance/UUID/<key>/UUID
    pattern1 = re.compile(r"(arn:aws:[^:]+:[^:]+:[^:]+:instance/[^/]+/([^/]+)/)[^/]+")
    pattern2 = re.compile(r"(arn:aws:[^:]+:[^:]+:[^:]+:instance/[^/]+(?:/([^/]+))?)")

    def replace_arn(value):
        """ 문자열에서 ARN을 찾아 변환 """
        if isinstance(value, str):
            v1 = pattern1.sub(r"***\2 ARN***", value)
            v2 = pattern2.sub(r"***Instance ARN***", v1)
            return v2
        elif isinstance(value, dict):
            return {k: replace_arn(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [replace_arn(v) for v in value]
        return value

    return replace_arn(log)

# 밀리초 차이 계산
def calculate_timestamp_gap(t1, t2):

    fmt = "%Y-%m-%dT%H:%M:%S.%fZ"

    dt1 = datetime.strptime(t1, fmt)
    dt2 = datetime.strptime(t2, fmt)

    millisecond_difference = int((dt1 - dt2).total_seconds() * 1000)
    return millisecond_difference

def get_bot_name_from_alias_arn(alias_arn: str) -> str:
    lex = boto3.client('lexv2-models')

    # ARN에서 botId, aliasId 추출
    match = re.match(r'arn:aws:lex:[\w-]+:\d+:bot-alias/([^/]+)/([^/]+)', alias_arn)
    if not match:
        raise ValueError("Invalid Lex Bot Alias ARN")

    bot_id, alias_id = match.groups()

    # Alias 정보 조회
    alias_info = lex.describe_bot_alias(
        botAliasId=alias_id,
        botId=bot_id
    )

    # Bot 정보 조회
    bot_info = lex.describe_bot(botId=bot_id)

    return bot_info['botName']