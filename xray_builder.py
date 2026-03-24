import os
import json

from graphviz import Digraph
from utils import get_xray_trace, wrap_text, apply_rank
from graph_labels import get_image_label, get_node_label, get_module_name_ko, add_edges


def get_xray_edge_label(data):
    label = ""
    xlabel = ""

    if data.get("name") in ["SSM", "Connect", "SecretsManager", "SQS", "S3"]:
        if data["aws"].get("resource_names"):
            label += f"{data['aws']['operation']}\n{data['aws']['resource_names'][0].split('/')[-1]}"
        else:
            label += data["aws"]["operation"]
    elif data.get("name") == "DynamoDB":
        if data["aws"].get("table_name"):
            label += f"{data['aws']['operation']}\n{data['aws']['table_name']}"
        else:
            label += data["aws"]["operation"]
    elif "." in data.get("name"):  # URL
        label += f"{data['http']['request']['method']}\n{'/'.join(data['http']['request']['url'].split('/')[3:])}"
        if data["http"].get("response"):
            if not str(data["http"]["response"]["status"]).startswith("2"):
                xlabel = str(data["http"]["response"]["status"])
        elif data.get("cause"):
            if data["cause"].get("exceptions"):
                xlabel = data["cause"]["exceptions"][0]["message"]

    return label, xlabel


def get_segment_node(xray_dot, subdata, parent_id):
    icon_path = f"{os.getcwd()}/mnt/aws/{subdata.get('name')}.png"
    fallback_icon = f"{os.getcwd()}/mnt/aws/settings.png"
    node_icon = icon_path if os.path.isfile(icon_path) else fallback_icon

    xray_dot.node(
        subdata.get("id"),
        label=get_image_label(node_icon, subdata.get("name", ""), 50),
        shape="plaintext",
        URL=json.dumps(subdata, indent=2, ensure_ascii=False)
    )

    label, xlabel = get_xray_edge_label(subdata)
    if label:
        if xlabel:
            xray_dot.edge(
                parent_id + ":e", subdata.get("id") + ":w",
                headlabel=label, minlen="2", xlabel=xlabel, color='tomato', fontcolor='tomato'
            )
        else:
            xray_dot.edge(parent_id + ":e", subdata.get("id") + ":w", headlabel=label, minlen="2")
    else:
        xray_dot.edge(parent_id + ":e", subdata.get("id") + ":w")

    return xray_dot


def process_subsegments(xray_dot, json_data):
    skip_names = {"Overhead", "Dwell Time", "Lambda", "QueueTime", "Initialization"}
    if json_data.get("subsegments"):
        for data in json_data["subsegments"]:
            if data.get("name") in skip_names:
                continue
            if data.get("name") == "Invocation" or "Attempt" in data.get("name"):
                for subdata in data.get("subsegments", []):
                    if subdata.get("name") not in skip_names:
                        xray_dot = get_segment_node(xray_dot, subdata, json_data.get("id"))
            else:
                xray_dot = get_segment_node(xray_dot, data, json_data.get("id"))
    return xray_dot


def get_xray_parent_id(parent_id, xray_data):
    invocation_id = None

    if parent_id:
        for segment in xray_data:
            for i in segment.get("subsegments", []):
                if i["id"] == parent_id:
                    invocation_id = segment["parent_id"]
                    break

    if invocation_id:
        for segment in xray_data:
            for j in segment.get("subsegments", []):
                if j["id"] == invocation_id:
                    return segment["id"]

    return None


def build_xray_nodes(xray_trace_id, associated_lambda_logs, module_stack, contact_id):
    xray_dot = Digraph(comment=f"AWS Lambda Xray Trace : {xray_trace_id}")
    xray_dot.attr(
        rankdir="LR",
        label=f"xray_trace_id : {xray_trace_id}",
        labelloc="t", fontsize="24", forcelabels="true"
    )

    module_stack = module_stack or ""
    xray_trace_file = f"./virtual_env/xray_trace_{contact_id}{module_stack}__{xray_trace_id}"

    with open(f"./virtual_env/batch_xray_{xray_trace_id}.json", "r", encoding="utf-8") as f:
        xray_batch_json_data_list = json.loads(f.read())

    for xray_batch_json_data in xray_batch_json_data_list:
        xray_dot = process_subsegments(xray_dot, xray_batch_json_data)

        origin = xray_batch_json_data.get("origin", "")

        if xray_batch_json_data.get("subsegments"):
            for segment in xray_batch_json_data["subsegments"]:
                if segment["name"] in ("Overhead", "Lambda"):
                    if "AWS" in origin:
                        icon_path = f"{os.getcwd()}/mnt/aws/{origin.split('::')[1]}.png"
                    else:
                        icon_path = f"{os.getcwd()}/mnt/aws/{xray_batch_json_data.get('name')}.png"

                    node_icon = icon_path if os.path.isfile(icon_path) else f"{os.getcwd()}/mnt/aws/settings.png"
                    xray_dot.node(
                        xray_batch_json_data.get("id"),
                        label=get_image_label(node_icon, xray_batch_json_data.get("name"), 50),
                        shape="plaintext",
                        URL=json.dumps(xray_batch_json_data, indent=2, ensure_ascii=False)
                    )

            parent_id = get_xray_parent_id(xray_batch_json_data.get("parent_id"), xray_batch_json_data_list)
            if parent_id:
                xray_dot.edge(parent_id, xray_batch_json_data.get("id"))

    xray_nodes = []
    if associated_lambda_logs:
        xray_dot.node(
            xray_trace_id + "_raw_json",
            label=get_image_label(f"{os.getcwd()}/mnt/aws/CloudWatch.png", "Raw Json", 30),
            shape="plaintext",
            URL=json.dumps(associated_lambda_logs, indent=4, ensure_ascii=False)
        )

        for index, l in enumerate(associated_lambda_logs):
            color = 'tomato' if l.get("level") in ("ERROR", "WARN") else 'lightgray'
            ts = l.get("timestamp", "").replace(':', '').replace('.', '')
            node_id = f"{xray_trace_id}_{ts}_{index}"

            node_text = ""
            message = l.get("message", "")
            if "parameter" in message:
                param_json = l.get("parameters", {})
                for key in param_json:
                    kv = f"{key} : {param_json[key]}"
                    node_text += wrap_text(kv, is_just_cut=True, max_length=25) + "\n"
                if "lex" in message:
                    node_text += f"intent : {l.get('intent', '')}"
            elif "attribute" in message:
                param_json = l.get("attributes", {})
                for key in param_json:
                    kv = f"{key} : {param_json[key]}"
                    node_text += wrap_text(kv, is_just_cut=True, max_length=25) + "\n"
            elif "lex" in message:
                node_text += message.replace("]", "]\n")
                node_text += l.get("event", {}).get("inputTranscript", "")
            else:
                node_text += message.replace("]", "]\n")

            level = l.get("level")
            if level == "WARN":
                node_title = f"⚠️   {level}"
            elif level == "ERROR":
                node_title = f"🚨   {level}"
            else:
                node_title = level

            block_id = message if "parameter" in message or "attribute" in message else " "
            xray_dot.node(
                node_id,
                label=get_node_label(level, node_title, wrap_text(node_text, is_just_cut=True, max_length=100), None, block_id),
                shape="plaintext",
                style='rounded,filled',
                color=color,
                URL=str(json.dumps(l, indent=4, ensure_ascii=False))
            )
            xray_nodes.append(node_id)

        xray_dot = add_edges(xray_dot, xray_nodes)
        if xray_nodes:
            apply_rank(xray_dot, xray_nodes)

        xray_dot.render(xray_trace_file, format="dot", cleanup=True)

    return xray_trace_file


def build_xray_dot(dot, nodes, error_count, xray_trace_id, region, function_logs, log, module_stack, contact_id):
    xray_trace = get_xray_trace(xray_trace_id, region)

    xray_text = ""
    if xray_trace:
        last_op = None
        index = 1
        for t in xray_trace:
            op = None
            if t.get("aws") and t["aws"].get("operation"):
                resource = (t["aws"]["resource_names"][0] if t["aws"].get("resource_names") else t["name"])
                op = f"{t['aws']['operation']} {resource}\n"

            if op != last_op:
                xray_text += f"Operation {index} : \n" + op
                last_op = op
                index += 1

    associated_lambda_logs = [l for l in function_logs if l.get("xray_trace_id") == xray_trace_id]

    xray_trace_file = build_xray_nodes(xray_trace_id, associated_lambda_logs, module_stack, contact_id)

    levels = [l.get("level", "INFO") for l in associated_lambda_logs]
    l_warn_count = levels.count("WARN")
    l_error_count = levels.count("ERROR")

    color = 'tomato' if l_error_count > 0 or l_warn_count > 0 else 'lightgray'
    lambda_node_footer = None
    if l_error_count > 0 or l_warn_count > 0:
        parts = []
        if l_warn_count > 0:
            parts.append(f"Warn : {l_warn_count}")
        if l_error_count > 0:
            parts.append(f"Error : {l_error_count}")
        lambda_node_footer = "\n".join(parts)

    ts = log.get("Timestamp", "").replace(":", "").replace(".", "")
    node_id = f"{ts}_{xray_trace_id}"

    dot.node(
        node_id,
        label=get_node_label(
            "xray",
            get_module_name_ko("xray", log) + "  ➡️",
            xray_text,
            lambda_node_footer,
            xray_trace_id
        ),
        shape="plaintext",
        style='rounded,filled',
        color=color,
        URL=f"{xray_trace_file}.dot"
    )

    nodes.append(node_id)

    if l_error_count > 0 or l_warn_count > 0:
        error_count += l_error_count + l_warn_count

    return dot, nodes, error_count
