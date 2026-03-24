import boto3
import json
import os
import re


def extract_ids_from_arn(arn):
    """ARN에서 instance_id 및 flow_id 또는 flow_module_id 추출"""
    match = re.match(
        r"arn:aws:connect:[a-z0-9-]+:\d+:instance/([a-f0-9-]+)/(?:(contact-flow|flow-module)/([a-f0-9-]+))?",
        arn
    )
    if match:
        instance_id = match.group(1)
        entity_type = match.group(2)
        entity_id = match.group(3) if match.group(3) else None
        return instance_id, entity_type, entity_id
    return None, None, None


def save_json(data, filename):
    """JSON 데이터를 파일로 저장"""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_contact_flow(flow_arn, region):
    """AWS Connect Contact Flow 정보를 가져와 JSON 파일로 저장"""
    instance_id, entity_type, flow_id = extract_ids_from_arn(flow_arn)
    if not instance_id or entity_type != "contact-flow" or not flow_id:
        raise ValueError(f"Invalid Contact Flow ARN : {flow_arn}")

    client = boto3.client("connect", region_name=region)
    response = client.describe_contact_flow(
        InstanceId=instance_id,
        ContactFlowId=flow_id
    )

    jsonfile_name = f"./virtual_env/describe_{entity_type}_{flow_id}.json"
    content = json.loads(response["ContactFlow"]["Content"])
    save_json(content, jsonfile_name)


def get_contact_flow_module(flow_module_arn, region):
    """AWS Connect Contact Flow Module 정보를 가져와 JSON 파일로 저장"""
    instance_id, entity_type, flow_module_id = extract_ids_from_arn(flow_module_arn)
    if not instance_id or entity_type != "flow-module" or not flow_module_id:
        raise ValueError(f"Invalid Contact Flow Module ARN : {flow_module_arn}")

    client = boto3.client("connect", region_name=region)
    response = client.describe_contact_flow_module(
        InstanceId=instance_id,
        ContactFlowModuleId=flow_module_id
    )

    jsonfile_name = f"./virtual_env/describe_{entity_type}_{flow_module_id}.json"
    content = json.loads(response["ContactFlowModule"]["Content"])
    save_json(content, jsonfile_name)


def get_contact_attributes(file_name):
    """AWS Connect Contact Attributes 정보 가져오기"""
    file_path = f"./virtual_env/{file_name}"
    if not os.path.isfile(file_path):
        return None

    with open(file_path, "r", encoding="utf-8") as f:
        flow_json = json.loads(f.read())

    comparison_values = {}
    for action in flow_json["Actions"]:
        action_type = action.get("Type")
        if action_type == 'UpdateContactData':
            result_data = {k: v for k, v in action.get("Parameters").items() if "$." in v}
            comparison_values.update(result_data)
        elif action_type == 'UpdateContactAttributes':
            result_data = {k: v for k, v in action.get("Parameters")["Attributes"].items() if "$." in v}
            comparison_values.update(result_data)
        elif action_type == 'UpdateFlowAttributes':
            dict_obj = action.get("Parameters")["FlowAttributes"]
            for key, value in dict_obj.items():
                if "$." in value["Value"]:
                    comparison_values[key] = value["Value"]

    return comparison_values


def _load_target_block(flow_module_arn, block_id):
    """flow JSON에서 block_id에 해당하는 액션 블록을 반환"""
    instance_id, entity_type, flow_id = extract_ids_from_arn(flow_module_arn)
    jsonfile_name = f"./virtual_env/describe_{entity_type}_{flow_id}.json"
    with open(jsonfile_name, encoding="utf-8") as file:
        src = json.load(file)
    return [action for action in src["Actions"] if action["Identifier"] == block_id]


def get_comparison_value(flow_module_arn, block_id, comparison_keyword):
    """flow JSON에서 특정 블록의 comparison 값을 반환"""
    target_block = _load_target_block(flow_module_arn, block_id)
    if target_block:
        return target_block[0]["Parameters"].get(comparison_keyword)
    return None


def get_comparison_second_value(flow_module_arn, block_id):
    """flow JSON에서 특정 블록의 Transitions Condition 값을 반환"""
    target_block = _load_target_block(flow_module_arn, block_id)
    if target_block:
        target_value = target_block[0]["Transitions"]["Conditions"][0]["Condition"]["Operands"][0]
        return target_value if "$" in target_value else None
    return None
