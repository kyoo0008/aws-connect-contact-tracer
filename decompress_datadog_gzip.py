import boto3
import gzip
import io
import json
import os
import sys
import csv
import re
import pytz
import datetime
from datetime import datetime, timedelta



log_pattern = re.compile(r"\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}")

region="ap-northeast-2"

output_dir = './s3/'  # 로컬에 저장할 출력 디렉토리

contact_ids = set()
file_names = set()

# S3 클라이언트 생성
s3_client = boto3.client('s3', region_name="ap-northeast-2")

def get_contact_timestamp(contact_id,region,instance_id,env):
    """AWS Connect Contact Flow 정보를 가져와 JSON 파일로 저장"""

    client = boto3.client("connect", region_name=region)

    response = client.describe_contact(
        InstanceId=instance_id,
        ContactId=contact_id
    )

    # init -10분, disconnect +10분
    initiation_time = datetime.fromisoformat(str(response["Contact"]["InitiationTimestamp"])).astimezone(pytz.UTC) - timedelta(minutes=10)
    disconnect_time = datetime.fromisoformat(str(response["Contact"]["DisconnectTimestamp"])).astimezone(pytz.UTC) + timedelta(minutes=10)

    return initiation_time.replace(tzinfo=None),disconnect_time.replace(tzinfo=None)


# S3에서 Gzip 파일을 다운로드하고 압축을 푼 후 처리하는 함수
def decompress_gzip_from_s3(bucket_name, s3_key):
    
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

# S3 경로에서 모든 파일을 다운로드하여 처리하는 함수
def decompress_datadog_logs(env, contact_id, instance_id):
    print(contact_id)
    bucket_name = f"aicc-{env}-an2-s3-adf-datadog-backup"

    # 출력 디렉토리가 없으면 생성
    os.makedirs(output_dir, exist_ok=True)

    logs = []

    initiation_time,disconnect_time = get_contact_timestamp(contact_id,region,instance_id,env)


    prefix="/".join(str(initiation_time).split(" ")[0].split("-"))


    response = s3_client.list_objects_v2(Bucket=bucket_name,Prefix=prefix)

    s3_keys = []
    for obj in response.get('Contents', []):
        s3_key = obj['Key']
        
        # S3 객체가 Gzip 파일인 경우에만 처리
        try:
            match = log_pattern.search(s3_key)
            if not match:
                continue
            log_time = datetime.strptime(match.group(), "%Y-%m-%d-%H-%M-%S").replace(tzinfo=None)

            if initiation_time <= log_time <= disconnect_time:
                s3_keys.append(s3_key)


        except Exception as e:
            print(f"Skipping non-gzip file {s3_key} : {e}")

    for key in s3_keys:
    # Gzip 파일을 복원하여 처리
        decompressed_text = decompress_gzip_from_s3(bucket_name, key)

        decompressed_text = decompressed_text.replace("}{","}\n{")

        # if "serialNumber" not in decompressed_text: # 키워드 검색
        #     continue

        # print(f"Processing file: {key}") 

        # with open(f"{output_dir}/{key.split("/")[4]}", "w", encoding="utf-8") as f:
        #     f.write(decompressed_text)

        # f.close()
        data = decompressed_text.splitlines()
        for line in data:
            json_data = json.loads(line)
            try:
                if contact_id in line and json_data.get("logGroup"):
                    if "/aws/connect/kal-servicecenter" in json_data.get("logGroup"):
                        for event in json_data['logEvents']:

                            message = json.loads(event.get("message"))
                            logs.append(message)
                            if message.get("ContactId"):
                                contact_ids.add(message.get("ContactId"))



            except Exception as e: 
                print(e)

    logs = sorted(logs, key=lambda x : x["Timestamp"], reverse=False)

    for c_id in contact_ids:

        c_logs = []
        for l in logs:
            if l["ContactId"] == c_id:
                c_logs.append(l)

        # JSON 파일 저장    
        output_json_path = f"./virtual_env/contact_flow_{c_id}.json"        

        if len(c_logs) > 0:
            with open(output_json_path, "w", encoding="utf-8") as json_file:
                json.dump(c_logs, json_file, ensure_ascii=False, indent=4)
                print(f"{output_json_path} saved!!!")
    return logs

    
def single_int_to_str(i):
    return "0"+str(i) if len(str(i))==1 else str(i)



# S3 경로에서 파일 다운로드 및 처리
# decompress_datadog_logs(bucket_name, contact_id)


