import json
import sys
import traceback

from datetime import datetime
from collections import defaultdict
from graphviz import Digraph

from utils import apply_rank, get_func_name, calculate_timestamp_gap
from graph_labels import (
    get_node_label, get_module_name_ko, get_node_text_by_module_type,
    define_module_type, add_edges
)
from xray_builder import build_xray_dot
from constants import ERROR_KEYWORDS, DUP_CONTACT_FLOW_MODULE_TYPE, OMIT_CONTACT_FLOW_MODULE_TYPE


def is_lambda_error(log):
    """Lambda InvokeExternalResource 결과가 실패인지 확인"""
    if log.get('ContactFlowModuleType') == "InvokeExternalResource":
        try:
            return log.get("ExternalResults", {})["isSuccess"] == "false"
        except (KeyError, TypeError):
            return False
    return False


def add_node_cache(module_type, node_cache, node_id, log, is_error):
    """연속되는 중복노드 캐시 생성"""
    parameters = log.get('Parameters', {})
    unique_key = (log['ContactFlowName'], module_type)

    if unique_key in node_cache:
        node_cache[unique_key]['Parameters'].append(parameters)
    else:
        node_cache[unique_key] = {
            'id': node_id,
            'contact_flow_name': log['ContactFlowName'],
            'module_type': module_type,
            'timestamp': log['Timestamp'],
            'blockIdentifier': log['Identifier'],
            'Parameters': [parameters],
            'is_error': is_error
        }
    return node_cache


def dup_block_sanitize(node_cache, dot, nodes):
    """중복된 모듈 타입 노드들을 하나의 노드로 생성"""
    for key, node_data in node_cache.items():
        node_text, _ = get_node_text_by_module_type(
            node_data['module_type'],
            node_data,
            node_data.get("blockIdentifier")
        )
        module_type = define_module_type(node_data['module_type'], node_data.get("Parameters", {}))
        label = get_node_label(
            module_type,
            get_module_name_ko(module_type, node_data),
            node_text, None,
            node_data.get("blockIdentifier")
        )
        color = 'tomato' if node_data['is_error'] else 'lightgray'
        dot.node(
            node_data['id'], label=label,
            shape='box', style='rounded,filled', color=color,
            URL=str(json.dumps(node_data, indent=4, ensure_ascii=False))
        )
        nodes.append(node_data['id'])
    return dot, nodes


def add_block_nodes(module_type, log, is_error, dot, nodes, node_id, lambda_logs, error_count, module_stack, env, region):
    """일반 노드 처리"""
    dot.attr(rankdir="LR", nodesep="0.5", ranksep="0.5")
    color = 'tomato' if is_error else 'lightgray'

    node_text, node_footer = get_node_text_by_module_type(module_type, log, log.get("Identifier"))
    module_type = define_module_type(module_type, log.get("Parameters") or {})

    dot.node(
        node_id,
        label=get_node_label(
            module_type,
            get_module_name_ko(module_type, log),
            node_text, node_footer,
            log.get("Identifier")
        ),
        shape="plaintext",
        style='rounded,filled',
        color=color,
        URL=str(json.dumps(log, indent=4, ensure_ascii=False))
    )
    nodes.append(node_id)

    if module_type == "InvokeExternalResource" and lambda_logs:
        function_name = get_func_name(log.get("Parameters")["FunctionArn"], env)
        try:
            function_logs = lambda_logs.get(function_name, [])

            if not isinstance(function_logs, list):
                raise TypeError(f"Expected list for function_logs, got {type(function_logs).__name__}")

            contact_id = log.get("ContactId")
            log_parameters = (log.get("Parameters") or {}).get("Parameters", [])

            target_logs = []
            for l in function_logs:
                if l.get("ContactId") != contact_id:
                    continue
                message = l.get("message", "")
                if "parameter" in message:
                    func_param = json.dumps(l.get("parameters"), sort_keys=True)
                    log_param = json.dumps(log_parameters, sort_keys=True)
                    func_param = func_param.replace("id&v", "idnv")
                    log_param = log_param.replace("id&v", "idnv")
                    if log_param == func_param:
                        target_logs.append(l)
                elif "Event" in message:
                    if l.get("event"):
                        func_param = l["event"]["Details"]["Parameters"]
                        log_param = log_parameters
                        if func_param.get('varsConfig') is not None and log_param.get('varsConfig') is not None:
                            func_param = dict(func_param)
                            log_param = dict(log_param)
                            del func_param['varsConfig']
                            del log_param['varsConfig']
                        if json.dumps(log_param, sort_keys=True) == json.dumps(func_param, sort_keys=True):
                            target_logs.append(l)

            xid = ""
            if len(target_logs) > 1:
                min_gap = sys.maxsize
                for l in target_logs:
                    gap = calculate_timestamp_gap(log.get("Timestamp"), l.get("timestamp"))
                    if min_gap > gap:
                        min_gap = gap
                        xid = l.get("xray_trace_id")
            elif len(target_logs) == 1:
                xid = target_logs[0].get("xray_trace_id")
            else:
                print(f"===no target logs=== : {log}")

            if target_logs:
                dot, nodes, error_count = build_xray_dot(
                    dot, nodes, error_count, xid, region, function_logs, log, module_stack, contact_id
                )

        except Exception:
            print(traceback.format_exc())

    return dot, nodes, error_count


def process_sub_flow(flow_type, dot, nodes, l_nodes, l_name, node_id, l_logs, contact_id, lambda_logs, error_count, env, region):
    """flow 묶음 처리"""
    min_timestamp, max_timestamp = None, None
    module_error_count = 0

    for log in l_logs:
        timestamp = datetime.fromisoformat(log['Timestamp'].replace('Z', '+00:00'))
        if min_timestamp is None or timestamp < min_timestamp:
            min_timestamp = timestamp
        if max_timestamp is None or timestamp > max_timestamp:
            max_timestamp = timestamp

        if any(keyword in log.get('Results', '') for keyword in ERROR_KEYWORDS):
            error_count += 1
        if is_lambda_error(log):
            error_count += 1

    if flow_type == "module":
        flow_name = ""
        with open(f"./virtual_env/contact_flow_{contact_id}.json") as f:
            flow_logs = json.loads(f.read())

        flow_arn_list = [l for l in flow_logs if l.get("ContactFlowId") == log['ModuleExecutionStack'][1]]
        if flow_arn_list:
            flow_name = flow_arn_list[0].get("ContactFlowName")

        module_stack = f"__{flow_name}__{l_name}"
        sub_dot, _, module_error_count = build_module_detail(l_logs, l_name, lambda_logs, module_error_count, module_stack, env, region)
        node_title = "InvokeFlowModule"
        error_count += module_error_count
        sub_file = f"./virtual_env/{flow_type}_{contact_id}{module_stack}"

    elif flow_type == "flow":
        module_stack = f"__{l_name}"
        sub_dot, error_count = build_contact_flow_detail(l_logs, l_name, contact_id, lambda_logs, error_count, module_stack, env, region)
        node_title = "TransferToFlow"
        sub_file = f"./virtual_env/{flow_type}_{contact_id}_{node_id}{module_stack}"

    sub_dot.render(sub_file, format="dot", cleanup=True)

    l_nodes[l_name] = node_id

    if flow_type == "module":
        l_color = 'tomato' if module_error_count > 0 else 'lightgray'
        error_count_text = f"Errors: {module_error_count}" if module_error_count > 0 else ""
    else:
        l_color = 'tomato' if error_count > 0 else 'lightgray'
        error_count_text = f"Errors: {error_count}" if error_count > 0 else ""

    l_label = get_node_label(
        node_title,
        f"{l_name}  ➡️",
        f"{str(min_timestamp).replace('000+00:00', '')} ~ \n{str(max_timestamp).replace('000+00:00', '')}",
        f"Nodes : {len(l_logs)}\n" + error_count_text,
        None
    )

    dot.node(node_id, label=l_label, shape='box', style='rounded,filled', color=l_color, URL=f"{sub_file}.dot")
    nodes.append(node_id)

    return dot, nodes, l_nodes, error_count


def build_module_detail(logs, module_name, lambda_logs, module_error_count, module_stack, env, region):
    """MOD_로 시작하는 모듈의 세부 정보를 시각화하는 그래프를 생성합니다."""
    m_dot = Digraph(comment=f"Amazon Connect Module: {module_name}")
    m_dot.attr(rankdir="LR", label=module_name, labelloc="t", fontsize="24")

    logs.sort(key=lambda x: datetime.fromisoformat(x['Timestamp'].replace('Z', '+00:00')))
    nodes = []
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
            if node_cache and module_type != last_module_type:
                m_dot, nodes = dup_block_sanitize(node_cache, m_dot, nodes)
                node_cache = {}

            if module_type not in OMIT_CONTACT_FLOW_MODULE_TYPE:
                m_dot, nodes, module_error_count = add_block_nodes(
                    module_type, log, is_error, m_dot, nodes, node_id,
                    lambda_logs, module_error_count, module_stack, env, region
                )

    m_dot = add_edges(m_dot, nodes)
    apply_rank(m_dot, nodes)

    return m_dot, nodes, module_error_count


def build_contact_flow_detail(logs, flow_name, contact_id, lambda_logs, error_count, module_stack, env, region):
    """Contact Detail 흐름을 시각화하고 MOD_ 모듈에 대한 세부 그래프를 추가 생성합니다."""
    dot = Digraph(comment="Amazon Connect Contact Flow")
    dot.attr(rankdir="LR", label=flow_name, labelloc="t", fontsize="24")

    logs.sort(key=lambda x: datetime.fromisoformat(x['Timestamp'].replace('Z', '+00:00')))
    nodes = []
    module_nodes = {}
    node_cache = {}
    last_module_type = ""

    for index, log in enumerate(logs):
        is_error = any(keyword in log.get('Results', '') for keyword in ERROR_KEYWORDS) or is_lambda_error(log)
        node_id = f"{log['Timestamp'].replace(':', '').replace('.', '')}_{index}"
        module_type = log.get('ContactFlowModuleType')

        if "MOD_" in log['ContactFlowName']:
            module_name = log['ContactFlowName']
            if module_name not in module_nodes:
                module_logs = [l for l in logs if l['ContactFlowName'] == module_name]
                dot, nodes, module_nodes, error_count = process_sub_flow(
                    "module", dot, nodes, module_nodes, module_name, node_id,
                    module_logs, contact_id, lambda_logs, error_count, env, region
                )
            else:
                node_id = module_nodes[module_name]
        else:
            if module_type in DUP_CONTACT_FLOW_MODULE_TYPE:
                node_cache = add_node_cache(module_type, node_cache, node_id, log, is_error)
                last_module_type = log.get(module_type)
            else:
                if node_cache and module_type != last_module_type:
                    dot, nodes = dup_block_sanitize(node_cache, dot, nodes)
                    node_cache = {}

                if module_type not in OMIT_CONTACT_FLOW_MODULE_TYPE:
                    dot, nodes, error_count = add_block_nodes(
                        module_type, log, is_error, dot, nodes, node_id,
                        lambda_logs, error_count, module_stack, env, region
                    )

    dot = add_edges(dot, nodes)
    apply_rank(dot, nodes)

    return dot, error_count


def build_main_flow(logs, lambda_logs, contact_id, env, region):
    """메인 Contact 흐름을 시각화합니다."""
    main_flow_dot = Digraph(comment="Amazon Connect Contact Flow")
    main_flow_dot.attr(rankdir="LR")

    logs.sort(key=lambda x: datetime.fromisoformat(x['Timestamp'].replace('Z', '+00:00')))
    nodes = []
    flow_nodes = {}

    node_info = defaultdict(lambda: {"contact_flow_name": "", "subnode": []})

    for log in logs:
        node_id = f"{contact_id}_{log['node_id']}"
        if "MOD_" not in log['ContactFlowName']:
            node_info[node_id]["contact_flow_name"] = log['ContactFlowName']
        node_info[node_id]["subnode"].append(log)

    for node_id, info in node_info.items():
        error_count = 0
        main_flow_dot, nodes, flow_nodes, error_count = process_sub_flow(
            "flow", main_flow_dot, nodes, flow_nodes,
            info['contact_flow_name'], node_id, info["subnode"],
            contact_id, lambda_logs, error_count, env, region
        )

    main_flow_dot = add_edges(main_flow_dot, nodes)
    apply_rank(main_flow_dot, nodes)

    return main_flow_dot, nodes
