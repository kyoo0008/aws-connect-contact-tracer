import gzip
import io
import json
import re
import botocore
import boto3
import pytz
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache


log_pattern = re.compile(r"\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}")



@lru_cache(maxsize=128)
def get_contact_timestamp(contact_id,region,instance_id):
    """AWS Connect Contact Flow 정보를 가져와 JSON 파일로 저장"""

    client = boto3.client("connect", region_name=region)

    response = client.describe_contact(
        InstanceId=instance_id,
        ContactId=contact_id
    )

    # init -1분, disconnect +10분
    initiation_time = datetime.fromisoformat(str(response["Contact"]["InitiationTimestamp"])).astimezone(pytz.UTC) - timedelta(minutes=1)
    if response["Contact"].get("DisconnectTimestamp"):
        disconnect_time = datetime.fromisoformat(str(response["Contact"]["DisconnectTimestamp"])).astimezone(pytz.UTC) + timedelta(minutes=10)
        return initiation_time.replace(tzinfo=None),disconnect_time.replace(tzinfo=None)
    else:
        return initiation_time.replace(tzinfo=None),None

    

def get_analysis_object(env,contact_id,region,instance_id):
    
    """대화 내용을 가져와서 파일로 저장"""

    bucket_name = f"aicc-{env}-an2-s3-acn-storage"

    initiation_time,disconnect_time = get_contact_timestamp(contact_id,region,instance_id)

    prefix = "Analysis/Voice/"+"/".join(str(disconnect_time if disconnect_time else initiation_time).split(" ")[0].split("-"))+"/"+contact_id
    
    
    # S3 클라이언트 생성
    s3_client = boto3.client('s3', region_name=region)
    if env != "test":
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
            for obj in page.get('Contents', []):
                s3_key = obj['Key']

                if contact_id in s3_key:
                    print("Transcript Found")
                    try:
                        data = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
                        conversation_data = data['Body'].read().decode('utf-8')

                        transcript = json.loads(conversation_data).get('Transcript',[])

                        return transcript
                    except botocore.exceptions.ClientError as e:
                        error_code = e.response['Error']['Code']
                        print(f"❌ Failed to get transcript from S3: {error_code}")
                        if error_code == "AccessDenied":
                            print("🔒 Access denied. Likely due to KMS Decrypt permission or cross-region resource.")
                        elif error_code == "NoSuchKey":
                            print("📂 S3 key not found.")
                        else:
                            print(f"⚠️ Unhandled S3 error: {e}")
                        return []

                    except Exception as e:
                        print(f"❗ Unexpected error while fetching transcript: {e}")
                        return []

    return []

# S3에서 Gzip 파일을 다운로드하고 압축을 푼 후 처리하는 함수
def decompress_gzip_from_s3(s3_client, bucket_name, s3_key):
    try:
        # S3 객체 다운로드
        response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
        gzip_data = response['Body'].read()  # 파일에서 Gzip 바이너리 데이터를 읽어옵니다.

        # 메모리에서 gzip 데이터를 읽어옵니다.
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(gzip_data), mode='rb') as f:
                decompressed_data = f.read().decode('utf-8')  # 압축을 풀고 텍스트로 복원
        except Exception as e:
            print(f'gzip failed : {e}')
        return decompressed_data
    except botocore.exceptions.ClientError as e:
        error_code = e.response['Error']['Code']
        print(f"❌ Failed to get transcript from S3: {error_code}")
        if error_code == "AccessDenied":
            print("🔒 Access denied. Likely due to KMS Decrypt permission or cross-region resource.")
        elif error_code == "NoSuchKey":
            print("📂 S3 key not found.")
        else:
            print(f"⚠️ Unhandled S3 error: {e}")
        return ""

    except Exception as e:
        print(f"❗ Unexpected error while fetching transcript: {e}")
        return ""

def _download_and_parse(s3_client, bucket_name, key, contact_id):
    """단일 S3 키에 대해 다운로드 및 파싱을 수행하는 헬퍼 함수"""
    decompressed_text = decompress_gzip_from_s3(s3_client, bucket_name, key)

    if not decompressed_text:
        return [], [], set()

    # 파일 단위 early return: contact_id가 파일 전체에 없으면 스킵
    if contact_id not in decompressed_text:
        return [], [], set()

    decompressed_text = decompressed_text.replace("}{", "}\n{")

    logs = []
    datadog_lambda_logs = []
    lambda_log_groups = set()

    for line in decompressed_text.splitlines():
        if contact_id not in line:
            continue
        try:
            json_data = json.loads(line)
            log_group = json_data.get("logGroup")
            if not log_group:
                continue

            if "/aws/connect/kal-servicecenter" in log_group:
                for event in json_data['logEvents']:
                    message = json.loads(event.get("message"))
                    if message.get("ContactId") == contact_id:
                        logs.append(message)
            elif "/aws/lmd" in log_group:
                lambda_log_groups.add(log_group)
                for event in json_data['logEvents']:
                    message = json.loads(event.get("message"))
                    if message.get("ContactId") == contact_id:
                        datadog_lambda_logs.append(message)
        except Exception as e:
            print(e)

    return logs, datadog_lambda_logs, lambda_log_groups


# S3 경로에서 모든 파일을 다운로드하여 처리하는 함수
def decompress_datadog_logs(env, contact_id, instance_id, region):
    bucket_name = f"aicc-{env}-an2-s3-adf-datadog-backup"

    s3_client = boto3.client('s3', region_name=region)

    logs = []
    datadog_lambda_logs = []

    initiation_time, disconnect_time = get_contact_timestamp(contact_id, region, instance_id)

    prefix_list = set()
    prefix_list.add("/".join(str(disconnect_time).split(" ")[0].split("-")))
    prefix_list.add("/".join(str(initiation_time).split(" ")[0].split("-")))

    # 페이지네이션으로 전체 S3 키 수집
    s3_keys = []
    paginator = s3_client.get_paginator('list_objects_v2')
    for prefix in prefix_list:
        for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
            for obj in page.get('Contents', []):
                s3_key = obj['Key']
                try:
                    match = log_pattern.search(s3_key)
                    if not match:
                        continue
                    log_time = datetime.strptime(match.group(), "%Y-%m-%d-%H-%M-%S").replace(tzinfo=None)
                    if initiation_time <= log_time <= disconnect_time:
                        s3_keys.append(s3_key)
                except Exception as e:
                    print(f"Skipping non-gzip file {s3_key} : {e}")

    # 병렬 다운로드 및 파싱
    lambda_log_groups = set()
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(_download_and_parse, s3_client, bucket_name, key, contact_id): key
            for key in s3_keys
        }
        for future in as_completed(futures):
            try:
                partial_logs, partial_lambda, partial_groups = future.result()
                logs.extend(partial_logs)
                datadog_lambda_logs.extend(partial_lambda)
                lambda_log_groups.update(partial_groups)
            except Exception as e:
                print(f"Error processing {futures[future]}: {e}")

    logs = sorted(logs, key=lambda x: x["Timestamp"], reverse=False)
    datadog_lambda_logs = sorted(datadog_lambda_logs, key=lambda x: x["timestamp"], reverse=False)

    lambda_logs = {}
    for lambda_log_group in lambda_log_groups:
        function_name = lambda_log_group.split("/")[4]
        f_logs = [
            log for log in datadog_lambda_logs
            if function_name in log.get("service", "")
        ]
        lambda_logs[function_name] = f_logs

    # JSON 파일 저장
    output_json_path = f"./virtual_env/contact_flow_{contact_id}.json"
    lambda_output_json_path = f"./virtual_env/lambda_logs_{contact_id}.json"

    if len(logs) > 0:
        with open(output_json_path, "w", encoding="utf-8") as json_file:
            json.dump(logs, json_file, ensure_ascii=False, indent=4)
            print(f"{output_json_path} saved!!!")

    if len(lambda_logs) > 0:
        with open(lambda_output_json_path, "w", encoding="utf-8") as json_file:
            json.dump(lambda_logs, json_file, ensure_ascii=False, indent=4)
            print(f"{lambda_output_json_path} saved!!!")

    return logs, lambda_logs


