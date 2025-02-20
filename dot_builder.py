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
                    get_xray_trace
from describe_flow import get_comparison_value
import traceback

# Error 로 인식하는 Results Keyword
ERROR_KEYWORDS = [
    'Error', 'Failed', 'Timeout', 'Exception', 'No prompt provided',
    'Instance has reached concurrent Lambda thread access limit',
    'Unsupported', 'Invalid', 'not found', 'NotDone', 'MultipleFound'
]

# 반복되는 Flow Block 중복 제거 
DUP_CONTACT_FLOW_MODULE_TYPE = [
    'SetAttributes', 'SetFlowAttributes'
]

# 생략 Flow Block
OMIT_CONTACT_FLOW_MODULE_TYPE = [
    'InvokeFlowModule'
]




def load_flow_translation(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        flow_translation = json.load(f)
    return {item["en_name"]: item["ko_name"] for item in flow_translation}

flow_translation_map = load_flow_translation("./flow_ko_en.json")


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
            comparison_value = get_comparison_value(log.get("ContactFlowId"),block_id,False)
            comparison_second_value = get_comparison_value(log.get("ContactFlowId"),block_id,True)

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
                node_text += f"{wrap_text(f"{key} = {parameters[key]}",is_just_cut=True,max_length=25)} \n"

        if replaced_arn_log.get("ExternalResults"):
            node_footer = "ExternalResults : " + wrap_text(
                json.dumps(replaced_arn_log.get("ExternalResults"), indent=2, ensure_ascii=False),
                is_just_cut=True,
                max_length=30)
    elif module_type == "PlayPrompt" or module_type == "GetUserInput" or module_type == "StoreUserInput":
        param_str = param_json.get("Text")
        if param_str: 
            node_text += wrap_text(param_str)
        elif param_json.get("PromptSource"):
            prompt_wav = param_json.get("PromptLocation")
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

# node label 가져오기
def get_node_label(module_type, node_title, node_text, node_footer, block_id):


    # 아이콘 경로 설정
    # icon_path = f"/Users/ke-aicc/workspace/graphviz/json-to-graph/cloudwatch-json-test/mnt/img/{module_type}.png"

    icon_path = f"{os.getcwd()}/mnt/img/{module_type}.png"

    node_text = str(node_text).replace(">","＞").replace("<","＜").replace("\n","<br/>")

    node_footer = str(node_footer).replace(">","＞").replace("<","＜").replace("\n","<br/>")

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
def add_block_nodes(module_type, log, is_error, dot, nodes, node_id, lambda_logs, error_count):
    
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
    if module_type == "InvokeExternalResource":
        function_name = get_func_name(log.get("Parameters")["FunctionArn"])
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

                # To-do : xray response
                xray_trace = get_xray_trace(xray_trace_id)
                xray_text = ""
                if len(xray_trace) > 0:
                    # print(f"xray_trace : {xray_trace}")
                    last_op = None
                    index = 1
                    for t in xray_trace:
                        op = t["aws"]["operation"] + " " + t["aws"]["resource_names"][0] + '\n'
                        if op != last_op:
                            xray_text += f"Operation {index} : \n" + op
                            last_op = op
                            index += 1


                # xray_trace_id가 있는 관련 로그 찾기
                associated_lambda_logs = [l for l in function_logs if l.get("xray_trace_id") == xray_trace_id]

                # print(f"associated_lambda_logs :{associated_lambda_logs}")

                # xray trace dot
                # associated_lambda_logs = associated_lambda_logs.sort(key=lambda x: datetime.fromisoformat(x['timestamp'].replace('Z', '+00:00')))

                xray_nodes = []
                if len(associated_lambda_logs) > 0:

                    xray_dot = Digraph(comment=f"AWS Lambda Xray Trace : {xray_trace_id}")
                    xray_dot.attr(rankdir="LR", label=f"xray_trace_id : {xray_trace_id}", labelloc="t",fontsize="24")
                    

                    for index,l in enumerate(associated_lambda_logs):

                        color = 'tomato' if l.get("level") == "ERROR" or l.get("level") == "WARN" else 'lightgray'
                        node_id = f"{xray_trace_id}_{l.get("timestamp").replace(':', '').replace('.', '')}_{index}"

                        node_text = ""
                        if "parameter" in l.get("message",""):
                            param_json = l.get("parameters",{})
                            for key in param_json:
                                node_text += f"{wrap_text(f"{key} : {param_json[key]}",is_just_cut=True,max_length=25)}\n"
                        elif "attribute" in l.get("message",""):
                            param_json = l.get("attributes",{})
                            for key in param_json:
                                node_text += f"{wrap_text(f"{key} : {param_json[key]}",is_just_cut=True,max_length=25)}\n"
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

                    xray_trace_file = f"./virtual_env/xray_trace_{xray_trace_id}"
                    xray_dot.render(xray_trace_file, format="dot", cleanup=True)

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
                node_id = f"{log.get("Timestamp").replace(":","").replace(".","")}_{xray_trace_id}"

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
                    error_count += 1

        except Exception:
            print(check_log)
            print(traceback.format_exc())

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
def process_sub_flow(flow_type,dot,nodes,l_nodes,l_name,node_id,l_logs,contact_id,lambda_logs,error_count):

    min_timestamp, max_timestamp = None, None
    module_error_count = 0

    node_title = ""
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
        sub_dot, _, module_error_count = build_module_detail(l_logs, l_name,lambda_logs,module_error_count)
        node_title = "InvokeFlowModule"
        error_count += module_error_count


    elif flow_type == "flow":
        sub_dot,error_count = build_contact_flow_detail(l_logs,l_name,contact_id,lambda_logs,error_count)
        node_title = "TransferToFlow"

    

    sub_file = f"./virtual_env/{flow_type}_{contact_id}_{node_id}"
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
def build_module_detail(logs, module_name,lambda_logs,error_count):
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
                m_dot, nodes, error_count = add_block_nodes(module_type, log, is_error, m_dot, nodes, node_id, lambda_logs,error_count)

    # ✅ 중복된 모듈 타입 노드들을 하나의 노드로 생성
    # m_dot, nodes = dup_block_sanitize(node_cache, m_dot, nodes)
    
    m_dot = add_edges(m_dot,nodes)

    apply_rank(m_dot, nodes)

    return m_dot, nodes, error_count

def build_contact_flow_detail(logs, flow_name, contact_id, lambda_logs,error_count):
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
        parameters = log.get('Parameters', {})

        # ✅ 중복 모듈 타입이면 기존 노드에 parameter를 추가
        if log['ContactFlowName'].startswith("MOD_") or log['ContactFlowName'].startswith("99_MOD_"):
            module_name = log['ContactFlowName']

            if module_name not in module_nodes:  # 처음 등장한 모듈만 생성
                module_logs = [l for l in logs if l['ContactFlowName'] == module_name]

                dot,nodes,module_nodes,error_count = process_sub_flow(flow_type,dot,nodes,module_nodes,module_name,node_id,module_logs,contact_id,lambda_logs,error_count)
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
                    dot, nodes, error_count = add_block_nodes(module_type, log, is_error, dot, nodes, node_id, lambda_logs,error_count)

    # ✅ 중복된 모듈 타입 노드들을 하나의 노드로 생성
    # dot, nodes = dup_block_sanitize(node_cache, dot, nodes)

    # edge 추가 
    dot = add_edges(dot, nodes)

    # rank 통일 
    apply_rank(dot, nodes)

    return dot, error_count

def build_main_flow(logs, lambda_logs, contact_id):
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

        if not log['ContactFlowName'].startswith('MOD_') and not log['ContactFlowName'].startswith("99_MOD_"):
            node_info[node_id]["contact_flow_name"] = log['ContactFlowName']

        node_info[node_id]["subnode"].append(log)


    for index, node_id in enumerate(node_info.keys()):
        error_count = 0

        info = node_info[node_id]

        main_flow_dot,nodes,flow_nodes,error_count = process_sub_flow(flow_type,main_flow_dot,nodes,flow_nodes,info['contact_flow_name'],node_id,info["subnode"],contact_id,lambda_logs,error_count)

    main_flow_dot = add_edges(main_flow_dot, nodes)

    apply_rank(main_flow_dot, nodes)


    return main_flow_dot, nodes

# main 화면 생성 
def build_main_contacts(selected_contact_id,associated_contacts,initiation_timestamp,region,log_group):

    dot = Digraph("Amazon Connect Contact Flow", filename="contact_flow.gv")

    dot.attr(rankdir="LR")

    dot.node("start", label="Start", shape="Mdiamond")

    subgraphs = {}
    subgraph_nodes = {}


    root_contact_ids = {}
    for contact in associated_contacts["ContactSummaryList"]:

        contact_id = contact.get("ContactId")
        channel = contact.get("Channel")

        if not contact_id:
            continue  # ContactId가 없으면 무시
        label = f"Contact Id : {contact_id} ✅ \nChannel : {channel}" if selected_contact_id == contact_id else f"Contact Id : {contact_id} \nChannel : {channel}"
        subgraphs[contact_id] = Digraph(f"cluster_{contact_id}")
        subgraphs[contact_id].attr(label=label)

    for contact in associated_contacts["ContactSummaryList"]:
        contact_id = contact.get("ContactId")
        if not contact_id:
            continue

        logs, lambda_logs = fetch_logs(contact_id,initiation_timestamp,region,log_group)

        # Graph 생성 시작
        contact_graph, nodes = build_main_flow(logs, lambda_logs, contact_id)

        subgraphs[contact_id].subgraph(contact_graph)
        subgraph_nodes[contact_id] = nodes

    for contact in associated_contacts["ContactSummaryList"]:
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
            if prev_id and prev_id in subgraphs:    
                dot.edge(subgraph_nodes[prev_id][-1], subgraph_nodes[contact_id][0], label=contact.get("InitiationMethod")) 
        except Exception:
            print(traceback.format_exc())
        
    for key, value in root_contact_ids.items():
        if len(subgraph_nodes[key]) > 0:
            dot.edge("start", subgraph_nodes[key][0], label=value) 

    return dot
