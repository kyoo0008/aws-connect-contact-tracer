import boto3
import json
import re
import os

def extract_ids_from_arn(arn):
    """ARN에서 instance_id 및 flow_id 또는 flow_module_id 추출"""
    match = re.match(
        r"arn:aws:connect:[a-z0-9-]+:\d+:instance/([a-f0-9-]+)/(?:(contact-flow|flow-module)/([a-f0-9-]+))?",
        arn
    )
    if match:
        instance_id = match.group(1)
        entity_type = match.group(2)  # "contact-flow" 또는 "flow-module"
        entity_id = match.group(3) if match.group(3) else None
        return instance_id, entity_type, entity_id
    return None, None, None

def save_json(data, filename):
    """JSON 데이터를 파일로 저장"""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def get_contact_flow(flow_arn,region):
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

def get_contact_flow_module(flow_module_arn,region):
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

def get_contact_attributes(contact_id,region,file_name,instance_id):
    """AWS Connect Contact Attributes 정보 가져오기"""
    # client = boto3.client("connect", region_name=region)

    # response = client.get_contact_attributes(
    #     InstanceId=instance_id,
    #     InitialContactId=contact_id
    # )
    # attributes = json.loads(response["Attributes"])
    # for key in attributes.keys():
        
    # content = json.loads(file_name)
    file_path = f"./virtual_env/{file_name}"
    if os.path.isfile(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            flow_json = json.loads(f.read())
            comparison_values = {}
            for action in flow_json["Actions"]:
                if action.get("Type")  == 'UpdateContactData':
                    result_data = {key: value for key, value in action.get("Parameters").items() if "$." in value}
                    comparison_values = {**comparison_values,**result_data} 
                elif action.get("Type") == 'UpdateContactAttributes':
                    result_data = {key: value for key, value in action.get("Parameters")["Attributes"].items() if "$." in value}
                    comparison_values = {**comparison_values,**result_data} 
                elif action.get("Type") == 'UpdateFlowAttributes':
                    dict_obj = json.loads(json.dumps(action.get("Parameters")["FlowAttributes"]))
                    output_dict = {}

                    for key, value in dict_obj.items():
                        if "$." in value["Value"]:
                            output_dict[key] = value["Value"]

                    comparison_values = {**comparison_values,**output_dict} 
            return comparison_values
    else:
        return None



    


def get_comparison_value(flow_module_arn,block_id,is_second_value):
    instance_id, entity_type, flow_id = extract_ids_from_arn(flow_module_arn)

    jsonfile_name = f"./virtual_env/describe_{entity_type}_{flow_id}.json"

    target_block = None

    if not is_second_value:
        with open(jsonfile_name, encoding="utf-8") as file:
            src = json.load(file)
            target_block = [action for action in src["Actions"] if action["Identifier"] == block_id]

        if target_block:

            return target_block[0]["Parameters"].get("ComparisonValue")
        else:
            return None
    else:
        with open(jsonfile_name, encoding="utf-8") as file:
            src = json.load(file)
            target_block = [action for action in src["Actions"] if action["Identifier"] == block_id]

        if target_block:
            target_value = target_block[0]["Transitions"]["Conditions"][0]["Condition"]["Operands"][0]
            return target_value if "$" in target_value else None
        else:
            return None



# 사용 예시
# get_contact_flow("arn:aws:connect:ap-northeast-2:123412341234:instance/abcdefg-1234/contact-flow/xyz-789")
# get_contact_flow_module("arn:aws:connect:ap-northeast-2:123412341234:instance/abcdefg-1234/flow-module/xyz-789")