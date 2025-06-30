import re
import sys
import json
import time
import pytz
import boto3
import os.path
from datetime import datetime, timedelta
from collections import defaultdict
from graphviz import Digraph
from utils import generate_node_ids, \
                    sanitize_label, \
                    fetch_logs, \
                    check_kor, \
                    apply_rank, \
                    valid_uuid, \
                    wrap_text, \
                    replace_generic_arn, \
                    get_func_name, \
                    calculate_timestamp_gap, \
                    get_xray_trace, \
                    check_json_file_exists, \
                    filter_lambda_logs, \
                    wrap_transcript, \
                    find_lex_xray_timestamp
from describe_flow import get_comparison_value, \
                    get_contact_attributes
from fetch_data_from_s3 import get_analysis_object
import traceback

# To-do : 전체적인 node_id 정리, dot 파일 지정 형식도 정리 

# Error 로 인식하는 Results Keyword
ERROR_KEYWORDS = [
    'Error', 'Failed', 'Timeout', 'Exception', 'No prompt provided',
    'Instance has reached concurrent Lambda thread access limit',
    'Unsupported', 'Invalid', 'not found', 'NotDone', 'MultipleFound',
    'The Lambda Function Returned An Error.'
]

# 반복되는 Flow Block 중복 제거 
DUP_CONTACT_FLOW_MODULE_TYPE = [
    'SetAttributes', 'SetFlowAttributes'
]

# 생략 Flow Block
OMIT_CONTACT_FLOW_MODULE_TYPE = [
    'InvokeFlowModule'
]

# Associated Contact 조회 여부(True : 여러 관련된 Contact 조회, False : 입력된 하나의 Contact만 조회)
ASSOCIATED_CONTACTS_FLAG=True


def load_flow_translation(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        flow_translation = json.load(f)
    return {item["en_name"]: item["ko_name"] for item in flow_translation}

flow_translation_map = load_flow_translation("./mnt/flow_ko_en.json")


# ✅ Edge 추가 (노드의 index 순서대로)
def add_edges(dot, nodes):

    added_edges = set()

    """
    노드 리스트를 기반으로 에지를 추가하는 함수
    """
    for i in range(len(nodes) - 1):
        if (nodes[i], nodes[i + 1]) not in added_edges:
            dot.edge(nodes[i], nodes[i + 1], label=str(i))
            added_edges.add((nodes[i], nodes[i + 1]))

    return dot

# 한글 모듈 이름 가져오기 
def get_module_name_ko(module_type,log):
    module_name_ko = flow_translation_map.get(module_type, module_type)
    module_name_ko = f"{module_name_ko} x {len(log.get("Parameters", {}))}" if module_type in DUP_CONTACT_FLOW_MODULE_TYPE else module_name_ko
    return module_name_ko

# 모듈 타입 정의 
def define_module_type(module_type,param_json):
    

    if module_type == "SetContactFlow":
        flow_type = param_json.get("Type")
        if flow_type == "CustomerHold" or flow_type == "AgentHold":
            return "SetHoldFlow"
        elif flow_type == "CustomerWhisper" or flow_type == "AgentWhisper":
            return "SetWhisperFlow"
        elif flow_type == "CustomerQueue":
            return "SetCustomerQueueFlow"
        elif flow_type == "DefaultAgentUI":
            return "SetEventHook"
    else:
        return module_type

# 모듈 타입에 따른 node text 정의
def get_node_text_by_module_type(module_type,log,block_id):

    # Arn regex로 잘라서 replace
    replaced_arn_log = replace_generic_arn(log)

    node_text = ""
    node_footer = ""
    param_json = replaced_arn_log.get("Parameters",{})

    if module_type == "CheckAttribute":
        op = param_json.get("ComparisonMethod") # 연산자 
        value = param_json.get("Value") # 비교할 값 
        second_value = param_json.get("SecondValue") # Flow에서 들어온 값 

        value = wrap_text(value,is_just_cut=False,max_length=50)


        if log.get("ContactFlowId") and block_id:
            comparison_value = get_comparison_value(log.get("ContactFlowId"),block_id,"ComparisonValue",False)
            comparison_second_value = get_comparison_value(log.get("ContactFlowId"),block_id,"ComparisonValue",True)

        operand = ""
        if op == "Contains":
            operand = "⊃"
        elif op == "Equals":
            operand = "="
        elif op == "GreaterThan":
            operand = ">"
        elif op == "GreaterThanOrEqualTo":
            operand = "≧"
        elif op == "LessThan":
            operand = "<"
        elif op == "LessThanOrEqualTo":
            operand = "≦"
        elif op == "StartsWith":
            operand = "StartsWith"
        else:
            node_text = "Invalid Operator" 

        value = f"{value}"+ (f"({comparison_value})" if comparison_value else "")

        second_value = f"{second_value}"+ (f"({comparison_second_value})" if comparison_second_value else "")

        is_too_long = len(str(value)+str(second_value)) > 30

        if is_too_long:
            node_text += value +f" {operand} \n{second_value} ? "
        else:
            node_text += value +f" {operand} {second_value} ? "

        node_footer = "Results : " + replaced_arn_log.get('Results')
    elif module_type == "InvokeExternalResource" or module_type == "InvokeLambdaFunction":
                
        # Param 존재 시 
        if param_json.get("Parameters"): 
            parameters = param_json.get("Parameters")
            for key in parameters:
                parameter_attr = get_comparison_value(log.get("ContactFlowId"),block_id,"LambdaInvocationAttributes",False)
                
                node_text += f"{wrap_text(f"{key} = {parameters[key]}",is_just_cut=True,max_length=25)} {"("+parameter_attr.get(key)+")" if "$" in parameter_attr.get(key) else ""}\n"

        if replaced_arn_log.get("ExternalResults"):
            node_footer = "ExternalResults : " + json.dumps(replaced_arn_log.get("ExternalResults",""), indent=2, ensure_ascii=False)
            # wrap_text(
            #     json.dumps(replaced_arn_log.get("ExternalResults"), indent=2, ensure_ascii=False),
            #     is_just_cut=True,
            #     max_length=30)
        else:
            node_footer += replaced_arn_log.get("Results","")
    elif module_type == "PlayPrompt" or module_type == "GetUserInput" or module_type == "StoreUserInput":
        param_str = param_json.get("Text")
        if param_str: 
            param_str = param_str.replace(",",",\n").replace(".",".\n") 
            for line in param_str.split("\n"):
                if len(line) > 30:
                    l_arr = line.split(" ")
                    l_arr[int(len(l_arr)/2)] = l_arr[int(len(l_arr)/2)] + "\n"
                    node_text += " ".join(l_arr) + "\n"
                else:
                    node_text += line + "\n"
        elif param_json.get("PromptSource"):
            prompt_wav = param_json.get("PromptLocation")
            if len(prompt_wav.split("/")) > 2:
                node_text += f"음원재생 : \n {prompt_wav.split("/")[-2]+"/"+prompt_wav.split("/")[-1]}"
        

        if replaced_arn_log.get('Results'):
            node_footer = "Results : " + wrap_text(replaced_arn_log.get('Results'),is_just_cut=True,max_length=20)
    elif module_type == "TagContact":
                
        # Param 존재 시 
        if param_json.get("Tags"): 
            tags = param_json.get("Tags")

            for key in tags:
                node_text += f"{key} : {tags[key]} \n"

    elif module_type == "SetAttributes" or module_type == "SetFlowAttributes":
        for param in param_json:    
            node_text += f"{wrap_text(f"{param['Key']} = {param['Value']}",is_just_cut=True,max_length=30)} \n"
        
    elif module_type == "SetLoggingBehavior":
        node_text += f"LoggingBehavior = {param_json['LoggingBehavior']}"
    elif module_type == "SetContactFlow" or module_type == "SetContactData":
        for key in param_json:
            node_text += f"{key} : {param_json[key]} \n"
    elif module_type == "GetCustomerProfile":
        data = replaced_arn_log.get("ResultData")
        if data:
            node_text += "ProfileId: " + data['ProfileId']

        if replaced_arn_log.get('Results'):
            node_footer = "Results : " + replaced_arn_log.get('Results')
    elif module_type == "AssociateContactToCustomerProfile":
        node_text += f"{param_json['ProfileRequestData'][0]}\n{param_json['ProfileRequestData'][1]}"
    elif module_type == "Dial" or module_type == "Resume" or module_type == "ReturnFromFlowModule":
        node_text = ""
    else:

        for key in param_json:
            node_text += f"{wrap_text(f"{key} = {param_json[key]}",is_just_cut=True,max_length=25)} \n"


        if replaced_arn_log.get('Results'):
            node_footer = "Results : " + replaced_arn_log.get('Results')

    node_text = wrap_text(node_text,is_just_cut=True,max_length=100)

    return node_text, node_footer

# image label 가져오기
def get_image_label(icon_path,text,size):

    label = f"""<<table border="0" cellborder="0" cellspacing="0">
        <tr>
            <td bgcolor="white" width="{size}" height="{size}" fixedsize="true"><img scale="true" src="{icon_path}"/></td>
        </tr>
        <tr>
            <td bgcolor="white" width="100">{text}</td>
        </tr></table>>"""

    return label

# node label 가져오기
def get_node_label(module_type, node_title, node_text, node_footer, block_id):


    # 아이콘 경로 설정
    # icon_path = f"/Users/ke-aicc/workspace/graphviz/json-to-graph/cloudwatch-json-test/mnt/img/{module_type}.png"

    icon_path = f"{os.getcwd()}/mnt/img/{module_type}.png"

    node_text = str(node_text).replace(">","＞").replace("<","＜").replace("\n","<br/>")

    node_footer = str(node_footer).replace(">","＞").replace("<","＜").replace("\n","<br/>")

    if node_footer.startswith("ExternalResults"):
        if "\"isSuccess\": \"true\"" in node_footer:
            node_footer = "isSuccess: true ✅"
        elif "\"isSuccess\": \"false\"" in node_footer:
            node_footer = "isSuccess: false ❌"
        else:
            node_footer = wrap_text(
                node_footer,
                is_just_cut=True,
                max_length=30)
    else:
        if "false" in node_footer or "Fail" in node_footer:
            node_footer += " ❌"
        elif "true" in node_footer or "Success" in node_footer:
            node_footer += " ✅"
    

    # 상단 구역 (아이콘 + 한글명), 하단 구역 parameter
    top_label = f"""<<table border="0" cellborder="0" cellspacing="0">
        <tr>
            <td bgcolor="lightgray" width="30" height="30" fixedsize="true"><img scale="true" src="{icon_path}"/></td>
            <td bgcolor="lightgray" width="150">{node_title}</td>
        </tr>""" if os.path.isfile(icon_path) else f"""<<table border="0" cellborder="0" cellspacing="0">
        <tr>
            <td bgcolor="lightgray">{node_title}</td>
        </tr>"""

    block_id_label = "" if block_id == None or valid_uuid(block_id) else (f"""<tr><td colspan="2">{sanitize_label(block_id)}</td></tr>""" if os.path.isfile(icon_path) else f"""<tr><td>{sanitize_label(block_id)}</td></tr>""")

    bottom_label = f"""<tr>
            <td colspan="2" bgcolor="white">{sanitize_label(node_text)}</td>
        </tr>""" if os.path.isfile(icon_path) else f"""<tr>
            <td bgcolor="white">{sanitize_label(node_text)}</td>
        </tr>"""


    result_label = "</table>>" if node_footer == None or node_footer == "None" else (f"""<tr><td colspan="2">{node_footer}</td></tr></table>>""" if os.path.isfile(icon_path) else f"""<tr><td>{node_footer}</td></tr></table>>""")

    full_label = top_label + block_id_label + bottom_label + result_label

    return full_label

# 일반 노드 처리
def add_block_nodes(module_type, log, is_error, dot, nodes, node_id, lambda_logs, error_count, module_stack, env):
    
    dot.attr(rankdir="LR", nodesep="0.5", ranksep="0.5")

    color = 'tomato' if is_error else 'lightgray'

    node_text,node_footer = get_node_text_by_module_type(module_type, log, log.get("Identifier"))

    module_type = define_module_type(module_type,log.get("Parameters",{}))

    # 노드 추가
    dot.node(
        node_id,
        label=get_node_label(
            module_type, 
            get_module_name_ko(module_type,log),
            node_text,
            node_footer,
            log.get("Identifier")
        ),
        shape="plaintext",  # 테이블을 사용하기 위해 plaintext 사용
        style='rounded,filled',
        color=color,
        URL=str(json.dumps(log, indent=4, ensure_ascii=False))
    ) 

    nodes.append(node_id)

    check_log = None
    # AWS Lambda Xray trace 추적
    if module_type == "InvokeExternalResource" and len(lambda_logs) > 0:
        
        function_name = get_func_name(log.get("Parameters")["FunctionArn"], env)
        try:

            function_logs = lambda_logs.get(function_name, [])  # 안전한 접근

            if not isinstance(function_logs, list):
                raise TypeError(f"Expected list for function_logs, but got {type(function_logs).__name__}")

            contact_id = log.get("ContactId")
            log_parameters = log.get("Parameters", []).get("Parameters", [])

            # target_logs 찾기
            target_logs = [
                l for l in function_logs
                if l.get("ContactId") == contact_id and \
                json.dumps(log_parameters,
                    ensure_ascii=False, 
                    sort_keys=True).replace("\n","").replace(" ","") in json.dumps(l, ensure_ascii=False).replace("\n","").replace(" ","")
            ]

            target_logs = []
            

            for l in function_logs:
                check_log = l
                if l.get("ContactId") == contact_id:
                    if "parameter" in l.get("message","") :
                        func_param = json.dumps(l.get("parameters"),sort_keys=True)
                        log_param = json.dumps(log_parameters,sort_keys=True)
                        func_param = func_param.replace("id&v","idnv") # idnv 예외 처리
                        log_param = log_param.replace("id&v","idnv")

                        if log_param == func_param:
                            target_logs.append(l)
                    elif "Event" in l.get("message",""): # vars config 예외 처리 
                        
                        func_param = {}
                        log_param = sorted(log_parameters.items())
                        if l.get("event"):
                            func_param = l["event"]["Details"]["Parameters"]
                            log_param = log_parameters

                            if None != func_param.get('varsConfig') and None != log_param.get('varsConfig'):
                                del func_param['varsConfig']
                                del log_param['varsConfig']

                            func_param = json.dumps(func_param,sort_keys=True)
                            log_param = json.dumps(log_param,sort_keys=True)
                            
                            if log_param == func_param:
                                target_logs.append(l)
                        
                    

            min_gap = sys.maxsize
            xid = ""
            if len(target_logs) > 1: # 2개 이상인 경우 가장 가까운 timstamp 차이 계산 
                for l in target_logs:
                    gap = calculate_timestamp_gap(log.get("Timestamp"),l.get("timestamp"))
                    if min_gap > gap:
                        min_gap = gap
                        xid = l.get("xray_trace_id")
            elif len(target_logs) == 1:
                xid = target_logs[0].get("xray_trace_id")
            else:
                print(f"===no target logs=== : {log}")
                # print("===no target logs===")
                

            if target_logs: # x-ray 추적 처리 
                xray_trace_id = xid
                dot,nodes,error_count = build_xray_dot(dot,nodes,error_count,xray_trace_id,connect_region,function_logs,log,module_stack,contact_id)

                
        except Exception:
            print(check_log)
            print(traceback.format_exc())
        
    return dot, nodes, error_count

def get_segment_node(xray_dot,subdata,parent_id):
    icon_path = f"{os.getcwd()}/mnt/aws/{subdata.get("name")}.png"
    if os.path.isfile(icon_path):
        xray_dot.node(
            subdata.get("id"), 
            label=get_image_label(icon_path,subdata.get("name",""),50), 
            shape="plaintext",
            URL=json.dumps(subdata, indent=2, ensure_ascii=False)
        )
    else:
        xray_dot.node(
            subdata.get("id"), 
            label=get_image_label(f"{os.getcwd()}/mnt/aws/settings.png",subdata.get("name",""),50), 
            shape="plaintext",
            URL=json.dumps(subdata, indent=2, ensure_ascii=False)
        )
        
    label, xlabel = get_xray_edge_label(subdata)
    if label != "":
        if xlabel == "":
            xray_dot.edge(parent_id+":e",subdata.get("id")+":w",headlabel=label,minlen="2")
        else:
            xray_dot.edge(parent_id+":e",subdata.get("id")+":w",headlabel=label,minlen="2",xlabel=xlabel, color='tomato', fontcolor='tomato')
    else:
        xray_dot.edge(parent_id+":e",subdata.get("id")+":w")
    return xray_dot

def process_subsegments(xray_dot, json_data):
    if json_data.get("subsegments"):
        for data in json_data["subsegments"]:
            if data.get("name") in ["Overhead","Dwell Time", "Lambda", "QueueTime", "Initialization"]:
                continue
            if data.get("name") == "Invocation" or "Attempt" in data.get("name"):
                if len(data.get("subsegments",[])) > 0:
                    for subdata in data.get("subsegments"):
                        if subdata.get("name") in ["Overhead", "Dwell Time", "Lambda", "QueueTime", "Initialization"]:
                            continue
                        else:
                            xray_dot = get_segment_node(xray_dot,subdata,json_data.get("id"))            
            else:
                xray_dot = get_segment_node(xray_dot,data,json_data.get("id"))

            
    return xray_dot

def get_xray_edge_label(data):

    label = ""
    xlabel = ""

    if data.get("name") in ["SSM", "Connect", "SecretsManager", "SQS", "S3"] :
        
        if data["aws"].get("resource_names"):
            label += f"{data["aws"]["operation"]}\n{data["aws"]["resource_names"][0].split("/")[-1]}"
        else:
            label += data["aws"]["operation"]
    elif data.get("name") == "DynamoDB":
        if data["aws"].get("table_name"):
            label += f"{data["aws"]["operation"]}\n{data["aws"]["table_name"]}"
        else:
            label += f"{data["aws"]["operation"]}"
    elif "." in data.get("name"): # URL
        label += f"{data["http"]["request"]["method"]}\n{"/".join(data["http"]["request"]["url"].split("/")[3:])}"
        if data["http"].get("response"):
            if not str(data["http"]["response"]["status"]).startswith("2"):
                xlabel = str(data["http"]["response"]["status"])
        elif data.get("cause"):
            if data["cause"].get("exceptions"):
                xlabel = data["cause"]["exceptions"][0]["message"]


    return label, xlabel

def get_xray_parent_id(parent_id, xray_data):

    invocation_id = None

    if parent_id:
        for segment in xray_data:
            if segment.get("subsegments"):
                for i in segment.get("subsegments"):
                    if i["id"] == parent_id:
                        invocation_id = segment["parent_id"]
                        break

    if invocation_id:
        for segment in xray_data:
            if segment.get("subsegments"):
                for j in segment.get("subsegments"):
                    if j["id"] == invocation_id:
                        return segment["id"]

    return None
                        

def build_xray_nodes(xray_trace_id,associated_lambda_logs,module_stack,contact_id):
    xray_dot = Digraph(comment=f"AWS Lambda Xray Trace : {xray_trace_id}")
    
    xray_dot.attr(rankdir="LR", label=f"xray_trace_id : {xray_trace_id}", labelloc="t",fontsize="24",forcelabels="true")

    with open(f"./virtual_env/batch_xray_{xray_trace_id}.json", "r", encoding="utf-8") as f:
        xray_batch_json_data_list = json.loads(f.read())
        
        for xray_batch_json_data in xray_batch_json_data_list:

            xray_dot = process_subsegments(xray_dot,xray_batch_json_data) 
            
            origin = xray_batch_json_data.get("origin","")

            if xray_batch_json_data.get("subsegments"):
                for segment in xray_batch_json_data["subsegments"]:
                    if segment["name"] in ["Overhead","Lambda"]:

                        icon_path = ""
                        if "AWS" in origin:
                            icon_path = f"{os.getcwd()}/mnt/aws/{origin.split("::")[1]}.png"
                        else:
                            icon_path = f"{os.getcwd()}/mnt/aws/{xray_batch_json_data.get("name")}.png"

                        if os.path.isfile(icon_path):
                            
                            xray_dot.node(xray_batch_json_data.get("id"),
                                            label=get_image_label(icon_path,xray_batch_json_data.get("name"),50),
                                            shape="plaintext",
                                            URL=json.dumps(xray_batch_json_data,indent=2,ensure_ascii=False))
                        else:
                            xray_dot.node(xray_batch_json_data.get("id"),
                                            label=get_image_label(f"{os.getcwd()}/mnt/aws/settings.png",xray_batch_json_data.get("name"),50),
                                            shape="plaintext",
                                            URL=json.dumps(xray_batch_json_data,indent=2,ensure_ascii=False))
                parent_id = get_xray_parent_id(xray_batch_json_data.get("parent_id"),xray_batch_json_data_list)

                if parent_id:
                    xray_dot.edge(parent_id, xray_batch_json_data.get("id"))

    xray_nodes=[]
    if len(associated_lambda_logs) > 0:
        # CloudWatch.png
        xray_dot.node(
            xray_trace_id+"_raw_json",
            label=get_image_label(f"{os.getcwd()}/mnt/aws/CloudWatch.png","Raw Json",30),
            shape="plaintext",
            URL=json.dumps(associated_lambda_logs,indent=4,ensure_ascii=False)
            )

        for index,l in enumerate(associated_lambda_logs):

            color = 'tomato' if l.get("level") == "ERROR" or l.get("level") == "WARN" else 'lightgray'
            node_id = f"{xray_trace_id}_{l.get("timestamp").replace(':', '').replace('.', '')}_{index}"

            node_text = ""
            if "parameter" in l.get("message",""):
                param_json = l.get("parameters",{})
                for key in param_json:
                    node_text += f"{wrap_text(f"{key} : {param_json[key]}",is_just_cut=True,max_length=25)}\n"
                if "lex" in l.get("message",""):
                    node_text += f"intent : {l.get("intent","")}"
            elif "attribute" in l.get("message",""):
                param_json = l.get("attributes",{})
                for key in param_json:
                    node_text += f"{wrap_text(f"{key} : {param_json[key]}",is_just_cut=True,max_length=25)}\n"
            elif "lex" in l.get("message",""):
                node_text += l.get("message","").replace("]","]\n")
                node_text += l.get("event",{}).get("inputTranscript","")
            else:
                node_text += l.get("message","").replace("]","]\n")

            node_title = l.get("level")
            if l.get("level") == "WARN":
                node_title = f"⚠️   {l.get("level")}"
            elif l.get("level") == "ERROR":
                node_title = f"🚨   {l.get("level")}"


            # 노드 추가
            xray_dot.node(
            node_id,
            label=get_node_label(l.get("level"), node_title, wrap_text(node_text,is_just_cut=True,max_length=100),None,l.get("message","") if "parameter" in l.get("message","") or "attribute" in l.get("message","") else " "),
            shape="plaintext",  # 테이블을 사용하기 위해 plaintext 사용
            style='rounded,filled',
            color=color,
            URL=str(json.dumps(l, indent=4, ensure_ascii=False))
            ) 
            
            xray_nodes.append(node_id)

        xray_dot = add_edges(xray_dot, xray_nodes)

        if len(xray_nodes) > 0:
            apply_rank(xray_dot,xray_nodes)

        xray_trace_file = f"./virtual_env/xray_trace_{contact_id}{module_stack}__{xray_trace_id}"
        xray_dot.render(xray_trace_file, format="dot", cleanup=True)

    
    return xray_trace_file
    
def build_xray_dot(dot, nodes, error_count, xray_trace_id, connect_region, function_logs, log, module_stack, contact_id):
    xray_trace = get_xray_trace(xray_trace_id, connect_region)
    xray_text = ""
    if len(xray_trace) > 0:
        # print(f"xray_trace : {xray_trace}")
        last_op = None
        index = 1
        for t in xray_trace:
            # print(t)
            op = None 
            if t.get("aws"):
                if t["aws"].get("operation"):
                    if len(t["aws"].get("resource_names",[])) > 0:
                        op = t["aws"]["operation"] + " " + t["aws"]["resource_names"][0] + '\n'
                    else:
                        op = t["aws"]["operation"] + " " + t["name"] + '\n'

            if op != last_op:
                xray_text += f"Operation {index} : \n" + op
                last_op = op
                index += 1


    # xray_trace_id가 있는 관련 로그 찾기
    associated_lambda_logs = [l for l in function_logs if l.get("xray_trace_id") == xray_trace_id]

    # print(f"associated_lambda_logs :{associated_lambda_logs}")

    # xray trace dot
    # associated_lambda_logs = associated_lambda_logs.sort(key=lambda x: datetime.fromisoformat(x['timestamp'].replace('Z', '+00:00')))

    xray_trace_file = build_xray_nodes(xray_trace_id,associated_lambda_logs,module_stack,contact_id)
    

    # level 값 가져오기
    levels = [l.get("level", "INFO") for l in associated_lambda_logs]  # 기본값을 INFO로 설정
    l_warn_count = 0
    l_error_count = 0
    for l in levels:
        if l == "ERROR":
            l_error_count += 1
        elif l == "WARN":
            l_warn_count += 1
            
    color = 'tomato' if l_error_count > 0 or l_warn_count > 0 else 'lightgray'
    lambda_node_footer = ((f"Warn : {l_warn_count}" if l_warn_count > 0 else "") + (f"\nError : {l_error_count}" if l_error_count > 0 else "")) if l_error_count > 0 or l_warn_count > 0 else None
    node_id = f"{log.get("Timestamp","").replace(":","").replace(".","")}_{xray_trace_id}"

    # 노드 추가
    dot.node(
        node_id,
        label=get_node_label(
            "xray",
            get_module_name_ko("xray", log) + "  ➡️",
            xray_text,
            lambda_node_footer,
            xray_trace_id
        ),
        shape="plaintext",  # 테이블을 사용하기 위해 plaintext 사용
        style='rounded,filled',
        color=color,
        URL=f"{xray_trace_file}.dot"
    )

    nodes.append(node_id)

    if l_error_count > 0 or l_warn_count > 0:
        error_count += (l_error_count+l_warn_count)

    return dot, nodes, error_count

# ✅ 중복된 모듈 타입 노드들을 하나의 노드로 생성
def dup_block_sanitize(node_cache, dot, nodes):
    for key, node_data in node_cache.items():
        
        node_text, _ = get_node_text_by_module_type(
            node_data['module_type'],
            node_data,
            node_data.get("blockIdentifier"))

        module_type = define_module_type(node_data['module_type'],node_data.get("Parameters", {})) 

        label = get_node_label(
            module_type, 
            get_module_name_ko(module_type,node_data),
            node_text,
            None,
            node_data.get("blockIdentifier"))
        color = 'tomato' if node_data['is_error'] else 'lightgray'

        dot.node(node_data['id'], label=label, shape='box', style='rounded,filled', color=color, URL=str(json.dumps(node_data, indent=4, ensure_ascii=False)))
        nodes.append(node_data['id'])
    return dot, nodes

# 연속되는 중복노드 캐시 생성
def add_node_cache(module_type,node_cache, node_id, log, is_error):
    parameters = log.get('Parameters', {})  
    unique_key = (log['ContactFlowName'], module_type)

    if unique_key in node_cache:
        # 기존 노드가 있으면 파라미터 추가
        node_cache[unique_key]['Parameters'].append(parameters)
    else:
        # 새 노드 생성
        node_cache[unique_key] = {
            'id': node_id,
            'contact_flow_name': log['ContactFlowName'],
            'module_type': module_type,
            'timestamp': log['Timestamp'],
            'blockIdentifier': log['Identifier'],
            'Parameters': [parameters],  # 리스트로 저장
            'is_error': is_error
        }

    return node_cache

def is_lambda_error(log):
    if log.get('ContactFlowModuleType') == "InvokeExternalResource" :
        try:
            if log.get("ExternalResults")["isSuccess"] == "false":
                return True
        except KeyError:
            return False
        except TypeError:
            return False
    else:
        return False 

# flow 묶음 처리
def process_sub_flow(flow_type,dot,nodes,l_nodes,l_name,node_id,l_logs,contact_id,lambda_logs,error_count,env):

    min_timestamp, max_timestamp = None, None
    module_error_count = 0

    node_title = ""

    sub_file = ""

    for log in l_logs:
        timestamp = datetime.fromisoformat(log['Timestamp'].replace('Z', '+00:00'))

        if min_timestamp is None or timestamp < min_timestamp:
            min_timestamp = timestamp
        if max_timestamp is None or timestamp > max_timestamp:
            max_timestamp = timestamp

        if any(keyword in log.get('Results', '') for keyword in ERROR_KEYWORDS):
            error_count += 1

        # Lambda 예외 처리(result false)
        if is_lambda_error(log):
            error_count += 1



    # 서브 그래프 생성
    if flow_type == "module":

        flow_name = ""
        flow_logs = []
        with open(f"./virtual_env/contact_flow_{contact_id}.json") as f:
            flow_logs = json.loads(f.read())

        flow_arn_list = [l for l in flow_logs if l.get("ContactFlowId") == log['ModuleExecutionStack'][1]]
        if len(flow_arn_list) > 0:
            flow_name = flow_arn_list[0].get("ContactFlowName")

        module_stack = f"__{flow_name}__{l_name}"

        sub_dot, _, module_error_count = build_module_detail(l_logs, l_name,lambda_logs,module_error_count,module_stack,env) 
        node_title = "InvokeFlowModule"
        error_count += module_error_count

        sub_file = f"./virtual_env/{flow_type}_{contact_id}{module_stack}"

    elif flow_type == "flow":
        module_stack = f"__{l_name}"
        sub_dot,error_count = build_contact_flow_detail(l_logs,l_name,contact_id,lambda_logs,error_count,module_stack, env)
        node_title = "TransferToFlow"
        sub_file = f"./virtual_env/{flow_type}_{contact_id}_{node_id}{module_stack}"
    
    sub_dot.render(sub_file, format="dot", cleanup=True)

    # ✅ MOD_ 모듈 노드의 label 구성 (build_main_flow와 동일한 형식)
    # l_label = f"{l_name}  ➡️\n{str(min_timestamp).replace('000+00:00', '')} ~ \n{str(max_timestamp).replace('000+00:00', '')}\nErrors: {error_count}"
    # 모듈 노드 저장 (중복 생성 방지)
    l_nodes[l_name] = node_id

    l_color = ""

    if flow_type == "module":
        l_color = 'tomato' if module_error_count > 0 else 'lightgray'
    elif flow_type == "flow":
        l_color = 'tomato' if error_count > 0 else 'lightgray'

    error_count_text = ""
    if flow_type == "module":
        error_count_text = f"Errors: {module_error_count}" if module_error_count > 0 else ""
    elif flow_type == "flow":
        error_count_text = f"Errors: {error_count}" if error_count > 0 else ""

    l_label = get_node_label(
        node_title,
        f"{l_name}  ➡️",
        f"{str(min_timestamp).replace('000+00:00', '')} ~ \n{str(max_timestamp).replace('000+00:00', '')}",
        (f"Nodes : {len(l_logs)}\n") + error_count_text,
        None)


    dot.node(node_id, label=l_label, shape='box', style='rounded,filled', color=l_color, URL=f"{sub_file}.dot")
    nodes.append(node_id)  # 노드가 처음 생성될 때만 추가

    return dot, nodes, l_nodes, error_count

# Build Dot
def build_module_detail(logs, module_name,lambda_logs,module_error_count,module_stack, env):
    """
    MOD_로 시작하는 모듈의 세부 정보를 시각화하는 그래프를 생성합니다.
    """

    m_dot = Digraph(comment=f"Amazon Connect Module: {module_name}")
    m_dot.attr(rankdir="LR", label=module_name, labelloc="t",fontsize="24")


    logs.sort(key=lambda x: datetime.fromisoformat(x['Timestamp'].replace('Z', '+00:00')))

    nodes = []

    # 중복 방지용 캐시 (노드 ID -> 로그 데이터 리스트)
    node_cache = {}

    last_module_type = ""
    for index, log in enumerate(logs):
        is_error = any(keyword in log.get('Results', '') for keyword in ERROR_KEYWORDS) or is_lambda_error(log)
        if is_error:
            module_error_count += 1 

        node_id = f"{log['Timestamp'].replace(':', '').replace('.', '')}_{index}"

        module_type = log.get('ContactFlowModuleType')
        
        

        if module_type in DUP_CONTACT_FLOW_MODULE_TYPE:
            node_cache = add_node_cache(module_type, node_cache, node_id, log, is_error)
            last_module_type = log.get(module_type)
        else:

            # 중복 노드 처리 
            if len(node_cache)>0 and module_type != last_module_type:
                m_dot, nodes = dup_block_sanitize(node_cache, m_dot, nodes)
                node_cache = {}

            if module_type not in OMIT_CONTACT_FLOW_MODULE_TYPE:
                m_dot, nodes, module_error_count = add_block_nodes(module_type, log, is_error, m_dot, nodes, node_id, lambda_logs,module_error_count,module_stack, env)

    # ✅ 중복된 모듈 타입 노드들을 하나의 노드로 생성
    # m_dot, nodes = dup_block_sanitize(node_cache, m_dot, nodes)
    
    m_dot = add_edges(m_dot,nodes)

    apply_rank(m_dot, nodes)

    return m_dot, nodes, module_error_count

def build_lex_dot(contact_id,connect_region):
    lex_nodes=[]
    file_path = f"./virtual_env/lex_{contact_id}.json"
    lex_transcript = []
    # Transcript 생성
    if os.path.isfile(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            lex_transcript = json.loads(f.read())
    else:
        return []

    # To-do : xray 여러개 뽑기
    lex_hook_func_log_path = f"./virtual_env/lex_hook_{contact_id}.json"
    if os.path.isfile(lex_hook_func_log_path):
        with open(lex_hook_func_log_path, "r", encoding="utf-8") as f:
            function_logs = json.loads(f.read())

    if len(lex_transcript) > 0:

        lex_dot = Digraph(comment = "Transcript")
        lex_dot.attr(rankdir="LR")
        
        

        for index,script in enumerate(lex_transcript):
            
            customer_node_id = script.get("requestId")+"-customer"
            # 고객 node
            lex_nodes.append(customer_node_id)

            intent_footer = ""

            max_intent_nlu_confidence_score = 0.0
            max_intent_nlu_confidence_name = ""


            for intent in script.get("interpretations",[]):
                # print(str(max_intent_nlu_confidence_score) + " / " + max_intent_nlu_confidence_name)
                if float(intent.get("nluConfidence",0)) > max_intent_nlu_confidence_score:
                    max_intent_nlu_confidence_score = float(intent.get("nluConfidence",0))
                    max_intent_nlu_confidence_name = intent.get("intent",{})["name"]

            for intent in script.get("interpretations",[]):
                if max_intent_nlu_confidence_name == intent.get("intent",{})["name"]:
                    intent_footer += f"* {intent.get("intent",{})["name"]} : {intent.get("nluConfidence","")}\n"
                else:
                    intent_footer += f"{intent.get("intent",{})["name"]} : {intent.get("nluConfidence","0.0")}\n"


            # 고객 Node
            lex_dot.node(
                customer_node_id,
                label=get_node_label(
                    "customer", 
                    "customer", 
                    wrap_transcript(script.get("inputTranscript","")), intent_footer, None),
                shape='box', 
                style='rounded,filled',
                color='lightgray',
                URL=str(json.dumps(script, indent=4, ensure_ascii=False))
            )

            
            # if "CodeHook" in str(json.dumps(script)) or :
            
            xray_trace_id = find_lex_xray_timestamp(script,function_logs)

            isTranscriptFound = False
            for l in function_logs:
                if l.get("xray_trace_id") == xray_trace_id and l.get("event",{}).get("inputTranscript") == script.get("inputTranscript"):
                    isTranscriptFound = True

            
            # xray_trace_id = xid
            if xray_trace_id != "" and isTranscriptFound:
                lex_dot,lex_nodes,_ = build_xray_dot(lex_dot,lex_nodes,0,xray_trace_id,connect_region,function_logs,{},None,contact_id)

            

            # AI 상담사 Node
            agent_node_id = script.get("requestId")+"-agent"

            lex_nodes.append(agent_node_id)
            agent_transcript = ""
            for message in script.get("messages",[]):
                agent_transcript += message.get("content","")



            lex_dot.node(
                agent_node_id,
                label=get_node_label(
                    "agent", 
                    "agent", 
                    wrap_transcript(agent_transcript), intent_footer, None),
                shape='box', 
                style='rounded,filled',
                color='lightgray',
                URL=str(json.dumps(script, indent=4, ensure_ascii=False))
            )

                
            

        lex_dot = add_edges(lex_dot, lex_nodes)
        apply_rank(lex_dot, lex_nodes)

        
            
        lex_dot.render(f"./virtual_env/lex_{contact_id}", format="dot", cleanup=True)
    return lex_nodes

def build_lex_hook_dot(contact_id,connect_region):
    
    nodes = []
    error_count = 0 
    
    lex_hook_dot = Digraph(comment = "Lex Hook")
    lex_hook_dot.attr(rankdir="LR")
    
    lex_hook_func_log_path = f"./virtual_env/lex_hook_{contact_id}.json"
    if os.path.isfile(lex_hook_func_log_path):
        with open(lex_hook_func_log_path, "r", encoding="utf-8") as f:
            function_logs = json.loads(f.read())
            
            xray_trace_ids = set()

            for log in function_logs:
                xray_trace_ids.add(log.get("xray_trace_id"))
                
            for xray_trace_id in xray_trace_ids:
                
                lex_hook_dot,nodes,error_count = build_xray_dot(lex_hook_dot,nodes,error_count,xray_trace_id,connect_region,function_logs,{},None,contact_id)


            lex_hook_dot = add_edges(lex_hook_dot, nodes)
            apply_rank(lex_hook_dot, nodes)
            lex_hook_dot.render(f"./virtual_env/lex_hook_{contact_id}", format="dot", cleanup=True)
                
                
        return nodes, error_count
    else:
        return [], 0

def build_transcript_dot(env,contact_id,region,instance_id):
    transcript_nodes=[]
    # Transcript 생성
    contact_transcript = get_analysis_object(env,contact_id,region,instance_id)
    if len(contact_transcript) > 0:


        transcript_dot = Digraph(comment = "Transcript")
        transcript_dot.attr(rankdir="LR")
        
        temp_dup_set = set()
        for index,script in enumerate(contact_transcript):
            
            if index+1 != len(contact_transcript) and script.get("ParticipantId") == contact_transcript[index+1].get("ParticipantId"):
                temp_dup_set.add(script.get("Id"))
                temp_dup_set.add(contact_transcript[index+1].get("Id"))

            else:
                
                if len(temp_dup_set) > 0:
                    temp_nodes = []
                    temp_script_content_arr = []
                    
                    for tid in temp_dup_set:
                        content = [l for l in contact_transcript if l.get("Id") == tid][0]
                        temp_nodes.append(content)
                        
                    temp_nodes = sorted(temp_nodes, key=lambda x:x['BeginOffsetMillis'])
                    for node in temp_nodes:
                        temp_script_content_arr.append(node.get("Content"))
                    
                    script_contents = "/".join(temp_script_content_arr)
                    
                    temp_dup_set=set()

                    node_id = temp_nodes[0].get("Id")
                    label=get_node_label(
                        temp_nodes[0].get("ParticipantId").lower(), 
                        temp_nodes[0].get("ParticipantId").lower(), 
                        wrap_transcript(script_contents), None, None)
                    detail=str(json.dumps(temp_nodes, indent=4, ensure_ascii=False))

                else:
                    node_id = script.get("Id")
                    label=get_node_label(
                        script.get("ParticipantId").lower(), 
                        script.get("ParticipantId").lower(), 
                        wrap_transcript(script.get("Content")), None, None)
                    script_contents = script
                    detail=str(json.dumps(script_contents, indent=4, ensure_ascii=False))


                transcript_nodes.append(node_id)

                transcript_dot.node(
                    node_id,
                    label=label,
                    shape='box', 
                    style='rounded,filled',
                    color='lightgray',
                    URL=detail
                )

                
            

        transcript_dot = add_edges(transcript_dot, transcript_nodes)
        apply_rank(transcript_dot, transcript_nodes)

        transcript_dot.render(f"./virtual_env/transcript_{contact_id}", format="dot", cleanup=True)
    return transcript_nodes

def build_contact_flow_detail(logs, flow_name, contact_id, lambda_logs,error_count,module_stack,env):
    """
    Graphviz를 사용해 Contact Detail 흐름을 시각화하고,
    MOD_로 시작하는 모듈에 대한 세부 그래프를 추가 생성합니다.
    """
    dot = Digraph(comment="Amazon Connect Contact Flow")
    dot.attr(rankdir="LR", label=flow_name, labelloc="t", fontsize="24")

    logs.sort(key=lambda x: datetime.fromisoformat(x['Timestamp'].replace('Z', '+00:00')))
    nodes = []
    module_nodes = {}  # MOD_ 모듈 노드 저장 (중복 방지)

    # 중복 방지용 캐시 (노드 ID -> 로그 데이터 리스트)
    node_cache = {}
    flow_type = "module"
    last_module_type = ""

    for index, log in enumerate(logs):
        is_error = any(keyword in log.get('Results', '') for keyword in ERROR_KEYWORDS) or is_lambda_error(log)

        node_id = f"{log['Timestamp'].replace(':', '').replace('.', '')}_{index}"

        module_type = log.get('ContactFlowModuleType')


        # ✅ 중복 모듈 타입이면 기존 노드에 parameter를 추가
        # if log['ContactFlowName'].startswith("MOD_") or log['ContactFlowName'].startswith("99_MOD_"):
        if "MOD_" in log['ContactFlowName']:
            module_name = log['ContactFlowName']

            if module_name not in module_nodes:  # 처음 등장한 모듈만 생성
                module_logs = [l for l in logs if l['ContactFlowName'] == module_name]

                dot,nodes,module_nodes,error_count = process_sub_flow(flow_type,dot,nodes,module_nodes,module_name,node_id,module_logs,contact_id,lambda_logs,error_count, env)
            else:
                node_id = module_nodes[module_name]  # 기존 모듈 노드를 참조
        else:

            if module_type in DUP_CONTACT_FLOW_MODULE_TYPE:
                node_cache = add_node_cache(module_type, node_cache, node_id, log, is_error)
                last_module_type = log.get(module_type)
            else:

                # 중복 노드 처리 
                if len(node_cache)>0 and module_type != last_module_type:
                    dot, nodes = dup_block_sanitize(node_cache, dot, nodes)
                    node_cache = {}

                if module_type not in OMIT_CONTACT_FLOW_MODULE_TYPE:
                    dot, nodes, error_count = add_block_nodes(module_type, log, is_error, dot, nodes, node_id, lambda_logs,error_count,module_stack, env)

    # ✅ 중복된 모듈 타입 노드들을 하나의 노드로 생성
    # dot, nodes = dup_block_sanitize(node_cache, dot, nodes)

    # edge 추가 
    dot = add_edges(dot, nodes)

    # rank 통일 
    apply_rank(dot, nodes)

    return dot, error_count

def build_main_flow(logs, lambda_logs, contact_id, env):
    """메인 Contact 흐름을 시각화합니다."""
    main_flow_dot = Digraph(comment="Amazon Connect Contact Flow")
    main_flow_dot.attr(rankdir="LR")


    logs.sort(key=lambda x: datetime.fromisoformat(x['Timestamp'].replace('Z', '+00:00')))
    nodes = []
    flow_nodes = {}

    flow_type = "flow"
    

    node_info = defaultdict(lambda: {"contact_flow_name":"","subnode": []})

    for log in logs:
        node_id = f"{contact_id}_{log['node_id']}"

        # if not log['ContactFlowName'].startswith('MOD_') and not log['ContactFlowName'].startswith("99_MOD_"):
        if "MOD_" not in log['ContactFlowName']:
            node_info[node_id]["contact_flow_name"] = log['ContactFlowName']

        node_info[node_id]["subnode"].append(log)


    for index, node_id in enumerate(node_info.keys()):
        error_count = 0

        info = node_info[node_id]

        main_flow_dot,nodes,flow_nodes,error_count = process_sub_flow(flow_type,main_flow_dot,nodes,flow_nodes,info['contact_flow_name'],node_id,info["subnode"],contact_id,lambda_logs,error_count, env)

    main_flow_dot = add_edges(main_flow_dot, nodes)

    apply_rank(main_flow_dot, nodes)

    return main_flow_dot, nodes

# main 화면 생성 
def build_main_contacts(selected_contact_id,associated_contacts,initiation_timestamp,region,log_group,env,instance_id):
    global connect_region
    connect_region = region
    client = boto3.client("connect", region_name=region)

    search_contacts = associated_contacts["ContactSummaryList"] if ASSOCIATED_CONTACTS_FLAG else [l for l in associated_contacts["ContactSummaryList"] if l.get("ContactId") == selected_contact_id]

    dot = Digraph("Amazon Connect Contact Flow", engine="neato", filename="contact_flow.gv")

    dot.attr(rankdir="LR")

    dot.node("start", label="Start", shape="Mdiamond")

    subgraphs = {}
    subgraph_nodes = {}
    subcontact_logs = {}
    subcontact_lambda_logs = {}
    subcontact_attr = {}

    root_contact_ids = {}
    

    for contact in search_contacts:
        contact_id = contact.get("ContactId")

        channel = contact.get("Channel")

        if not contact_id:
            continue  # ContactId가 없으면 무시
        label = f"Contact Id : {contact_id} ✅ \nChannel : {channel}" if selected_contact_id == contact_id else f"Contact Id : {contact_id} \nChannel : {channel}"
        subgraphs[contact_id] = Digraph(f"cluster_{contact_id}")
        subgraphs[contact_id].attr(label=label)
        if not contact_id:
            continue

        logs, lambda_logs, contact_flow_ids = fetch_logs(contact_id,initiation_timestamp,region,log_group,env,instance_id)
        subcontact_logs[contact_id] = logs
        subcontact_lambda_logs[contact_id] = lambda_logs

        response = client.get_contact_attributes(
            InstanceId=instance_id,
            InitialContactId=contact_id
        )

        contact_attrs = response["Attributes"]


        data = []

        for k,v in contact_attrs.items():
            is_exists = False
            for log in logs:
                if log.get("ContactFlowModuleType") == "SetAttributes" and "Parameters" in log:
                    key = log["Parameters"].get("Key")
                    value = log["Parameters"].get("Value")
                    if key == k:
                        is_exists = True
                        break
            if is_exists == False:
                data.append({
                    "k": k,
                    "v": json.dumps(v,ensure_ascii=False),
                    "c": "",
                    "i": ""
                })
            else:
                data.append({
                    "k": k,
                    "v": json.dumps(v,ensure_ascii=False),
                    "c": log.get("ContactFlowName"),
                    "i": log.get("Identifier")
                })

        subcontact_attr[contact_id] = data

    for my_id, my_data in subcontact_attr.items():
        for entry in my_data:
            if entry["v"] and entry["c"] and entry["i"]:  
                continue

            for other_id, other_data in subcontact_attr.items():
                if other_id == my_id:
                    continue  

                for other_entry in other_data:
                    if other_entry["k"] == entry["k"] and other_entry["v"] == entry["v"]:

                        entry["v"] = other_entry["v"]
                        entry["c"] = other_entry["c"]
                        entry["i"] = other_entry["i"]
                        break  


    for contact in search_contacts:
        contact_id = contact.get("ContactId")
        logs = subcontact_logs[contact_id]
        lambda_logs = subcontact_lambda_logs[contact_id]

        # Graph 생성 시작
        contact_graph, nodes = build_main_flow(logs, lambda_logs, contact_id, env)

        # Transcript Node 생성 
        transcript_nodes = build_transcript_dot(env,contact_id,region,instance_id)

        if len(transcript_nodes) > 0:
            contact_graph.node(
                contact_id+"_transcript",
                label=get_image_label(f"{os.getcwd()}/mnt/img/transcript.png","Transcript",30),
                shape="plaintext",
                URL=f"./virtual_env/transcript_{contact_id}.dot"
                )

        lex_nodes = build_lex_dot(contact_id,connect_region)

        if len(lex_nodes) > 0:
            contact_graph.node(
                contact_id+"_lex_script",
                label=get_image_label(f"{os.getcwd()}/mnt/aws/Lex.png","Lex",30),
                shape="plaintext",
                URL=f"./virtual_env/lex_{contact_id}.dot"
                )

        lex_hook_nodes, _ = build_lex_hook_dot(contact_id,connect_region)

        if len(lex_hook_nodes) > 0:
            contact_graph.node(
                contact_id+"_lex_hook",
                label=get_image_label(f"{os.getcwd()}/mnt/aws/Lambda.png","Lex Hook",30),
                shape="plaintext",
                URL=f"./virtual_env/lex_hook_{contact_id}.dot"
                )


                
        contact_graph.node(
                contact_id+"_attributes",
                label=get_image_label(f"{os.getcwd()}/mnt/img/SetAttributes.png","Attributes",30),
                shape="plaintext",
                # URL=str(json.dumps(response["Attributes"], indent=2, sort_keys=True, ensure_ascii=False))
                URL=f'{subcontact_attr[contact_id]}'
                )
        


        subgraphs[contact_id].subgraph(contact_graph)
        subgraph_nodes[contact_id] = nodes

        

    # edge 추가

    for contact in search_contacts :
        contact_id = contact.get("ContactId")
        prev_id = contact.get("PreviousContactId") 
        related_id = contact.get("RelatedContactId")

        if not contact_id:
            continue

        if not prev_id:
            root_contact_ids[contact_id] = contact.get("InitiationMethod")

        if related_id:
            root_contact_ids[contact_id] = contact.get("InitiationMethod")

        dot.subgraph(subgraphs[contact_id])

        # prev contact id의 마지막 node -> contact id의 첫번째 노드 edge
        try:
            if related_id and len(subgraph_nodes[related_id]) > 0 and len(subgraph_nodes[contact_id]) > 0:
                dot.edge(subgraph_nodes[related_id][-1], subgraph_nodes[contact_id][0], label="Related", dir="none") 
            elif prev_id and prev_id in subgraphs and len(subgraph_nodes[prev_id]) > 0 and len(subgraph_nodes[contact_id]) > 0:
                dot.edge(subgraph_nodes[prev_id][-1], subgraph_nodes[contact_id][0], label=contact.get("InitiationMethod")) 
        except Exception:
            print(traceback.format_exc())
            
    for key, value in root_contact_ids.items():
        if len(subgraph_nodes[key]) > 0:
            dot.edge("start", subgraph_nodes[key][0], label=value) 

    return dot

