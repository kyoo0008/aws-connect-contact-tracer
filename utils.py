import re
import sys
import json
import time
import pytz
import boto3
import os

from datetime import datetime, timedelta
from collections import defaultdict

from describe_flow import get_contact_flow, \
                        get_contact_flow_module

# 그래프에서 한 줄에 표시할 노드 수 
COLS_NUM = 5

# 그래프에서 제외할 Flow Name
EXCEPT_CONTACT_FLOW_NAME = [
    '99_MOD_Dummy', 'InvokeFlowModule'
]


# Util
def generate_node_ids(logs):
    logs.sort(key=lambda log: log['Timestamp'])  # timestamp 기준 정렬
    flow_indices = defaultdict(int)
    last_flow_name = None  # 마지막 유효한 Entry 노드의 flow_name 저장
    last_node_id = None  # 마지막 Entry 기반 node_id 저장

    for log in logs:
        flow_name = log['ContactFlowName']

        if last_flow_name and (flow_name.startswith("MOD_") or flow_name == last_flow_name):
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

def fetch_logs(contact_id, initiation_timestamp, region, log_group):
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

                # Lambda 함수 수집 
                if json_value.get("ContactFlowModuleType") == "InvokeExternalResource":
                    function_arn = json_value.get("Parameters")["FunctionArn"]
                    lambda_log_groups.add(get_lambda_log_groups_from_arn(function_arn))
                    if "idnv-common-if" in function_arn: # common-if 예외처리 
                        lambda_log_groups.add(get_lambda_log_groups_from_arn(function_arn.replace("common-if","async-if")))

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
                get_contact_flow(contact_flow_id)
        elif 'flow-module' in contact_flow_id:
            jsonfile_name = f"./virtual_env/describe_flow_module_{contact_flow_id}.json"

            if not os.path.isfile(jsonfile_name):
                get_contact_flow_module(contact_flow_id)

    lambda_logs = {}

    for lambda_log_group in lambda_log_groups:
        function_name = lambda_log_group.split("/")[4]
        lambda_logs[function_name] = fetch_lambda_logs(contact_id, initiation_timestamp, region, lambda_log_group)

    try:
        if len(lambda_logs['flow-idnv-async-if']) > 0:
            lambda_logs['flow-idnv-common-if'] += lambda_logs['flow-idnv-async-if']
    except Exception as e:
        print(e)

    return logs, lambda_logs

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


    logs = []

    if len(response["results"]) > 0:

        for result in response["results"]:
            for field in result:
                if field["field"] == "@message":
                    json_value = json.loads(field["value"])
                    logs.append(json_value)

    return logs

        # JSON 파일 저장    
        # output_json_path = f"./virtual_env/{log_group.split("/")[4]}_{contact_id}.json"
        # with open(output_json_path, "w", encoding="utf-8") as json_file:
        #     json.dump(logs, json_file, ensure_ascii=False, indent=4)

        # print(f"JSON 파일이 저장되었습니다: {output_json_path}")


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