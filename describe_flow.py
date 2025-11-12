"""
AWS Connect Contact Flow 및 Flow Module 정보 조회 모듈

이 모듈은 AWS Connect의 Contact Flow와 Flow Module 정보를
ARN을 통해 조회하고 저장하는 기능을 제공합니다.
"""
import json
import os
import re
from typing import Dict, Any, Optional, Tuple

import boto3


# Constants
OUTPUT_DIR = "./virtual_env"
ARN_PATTERN = re.compile(
    r"arn:aws:connect:[a-z0-9-]+:\d+:instance/([a-f0-9-]+)/"
    r"(?:(contact-flow|flow-module)/([a-f0-9-]+))?"
)


def extract_ids_from_arn(arn: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    ARN에서 instance_id 및 flow_id 또는 flow_module_id 추출

    Args:
        arn: AWS Connect ARN

    Returns:
        Tuple[instance_id, entity_type, entity_id]
        entity_type은 "contact-flow" 또는 "flow-module"
    """
    match = ARN_PATTERN.match(arn)
    if match:
        instance_id = match.group(1)
        entity_type = match.group(2)  # "contact-flow" 또는 "flow-module"
        entity_id = match.group(3) if match.group(3) else None
        return instance_id, entity_type, entity_id
    return None, None, None


def save_json(data: Dict[str, Any], filename: str) -> None:
    """
    JSON 데이터를 파일로 저장

    Args:
        data: 저장할 데이터
        filename: 저장할 파일 경로
    """
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def get_contact_flow(flow_arn: str, region: str) -> None:
    """
    AWS Connect Contact Flow 정보를 가져와 JSON 파일로 저장

    Args:
        flow_arn: Contact Flow ARN
        region: AWS 리전

    Raises:
        ValueError: ARN 형식이 유효하지 않은 경우
    """
    instance_id, entity_type, flow_id = extract_ids_from_arn(flow_arn)
    if not instance_id or entity_type != "contact-flow" or not flow_id:
        raise ValueError(f"Invalid Contact Flow ARN: {flow_arn}")

    client = boto3.client("connect", region_name=region)

    response = client.describe_contact_flow(
        InstanceId=instance_id,
        ContactFlowId=flow_id
    )

    jsonfile_name = f"{OUTPUT_DIR}/describe_{entity_type}_{flow_id}.json"
    content = json.loads(response["ContactFlow"]["Content"])
    save_json(content, jsonfile_name)


def get_contact_flow_module(flow_module_arn: str, region: str) -> None:
    """
    AWS Connect Contact Flow Module 정보를 가져와 JSON 파일로 저장

    Args:
        flow_module_arn: Contact Flow Module ARN
        region: AWS 리전

    Raises:
        ValueError: ARN 형식이 유효하지 않은 경우
    """
    instance_id, entity_type, flow_module_id = extract_ids_from_arn(flow_module_arn)
    if not instance_id or entity_type != "flow-module" or not flow_module_id:
        raise ValueError(f"Invalid Contact Flow Module ARN: {flow_module_arn}")

    client = boto3.client("connect", region_name=region)

    response = client.describe_contact_flow_module(
        InstanceId=instance_id,
        ContactFlowModuleId=flow_module_id
    )

    jsonfile_name = f"{OUTPUT_DIR}/describe_{entity_type}_{flow_module_id}.json"
    content = json.loads(response["ContactFlowModule"]["Content"])
    save_json(content, jsonfile_name)

def get_contact_attributes(contact_id: str, region: str, file_name: str,
                          instance_id: str) -> Optional[Dict[str, str]]:
    """
    AWS Connect Contact Attributes 정보 가져오기

    Args:
        contact_id: Contact ID
        region: AWS 리전
        file_name: Flow 정의 파일명
        instance_id: Connect Instance ID

    Returns:
        Attribute 딕셔너리 또는 None
    """
    file_path = f"{OUTPUT_DIR}/{file_name}"
    if not os.path.isfile(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            flow_json = json.load(f)

        comparison_values = {}
        for action in flow_json.get("Actions", []):
            action_type = action.get("Type")

            if action_type == 'UpdateContactData':
                result_data = {
                    key: value
                    for key, value in action.get("Parameters", {}).items()
                    if "$." in str(value)
                }
                comparison_values.update(result_data)

            elif action_type == 'UpdateContactAttributes':
                result_data = {
                    key: value
                    for key, value in action.get("Parameters", {}).get("Attributes", {}).items()
                    if "$." in str(value)
                }
                comparison_values.update(result_data)

            elif action_type == 'UpdateFlowAttributes':
                flow_attrs = action.get("Parameters", {}).get("FlowAttributes", {})
                output_dict = {
                    key: value["Value"]
                    for key, value in flow_attrs.items()
                    if "$." in value.get("Value", "")
                }
                comparison_values.update(output_dict)

        return comparison_values

    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error parsing flow file {file_path}: {e}")
        return None

def get_comparison_value(flow_module_arn: str, block_id: str,
                        comparison_keyword: str, is_second_value: bool) -> Optional[str]:
    """
    Flow Module에서 특정 블록의 비교 값을 조회

    Args:
        flow_module_arn: Flow Module ARN
        block_id: 블록 ID
        comparison_keyword: 비교 키워드
        is_second_value: 두번째 값 조회 여부

    Returns:
        비교 값 또는 None
    """
    instance_id, entity_type, flow_id = extract_ids_from_arn(flow_module_arn)
    jsonfile_name = f"{OUTPUT_DIR}/describe_{entity_type}_{flow_id}.json"

    if not os.path.isfile(jsonfile_name):
        return None

    try:
        with open(jsonfile_name, encoding="utf-8") as file:
            src = json.load(file)

        target_block = [
            action for action in src.get("Actions", [])
            if action.get("Identifier") == block_id
        ]

        if not target_block:
            return None

        if not is_second_value:
            return target_block[0].get("Parameters", {}).get(comparison_keyword)
        else:
            # Second value 조회 (조건문에서 사용)
            try:
                transitions = target_block[0].get("Transitions", {})
                conditions = transitions.get("Conditions", [{}])
                condition = conditions[0].get("Condition", {})
                operands = condition.get("Operands", [])

                if operands:
                    target_value = operands[0]
                    return target_value if "$" in str(target_value) else None
            except (KeyError, IndexError):
                return None

    except (json.JSONDecodeError, IOError) as e:
        print(f"Error reading comparison value from {jsonfile_name}: {e}")
        return None