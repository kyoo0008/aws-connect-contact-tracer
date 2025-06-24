import boto3
import json
import time
import sys
from datetime import datetime,timedelta
from graphviz import Digraph
import pytz
import re
from collections import defaultdict
from dot_window import MainDotWindow
from dot_builder import build_main_contacts
# gtk
import gi
gi.require_version('Gtk', '3.0')

from gi.repository import Gtk

import xdot.ui


LOG_GROUP = f'/aws/connect/{sys.argv[1]}'
INSTANCE_ID = sys.argv[2]
SELECTED_CONTACT_ID = sys.argv[3]  # 찾고자 하는 ContactId
REGION = sys.argv[4] # AWS Region 및 로그 그룹 이름 설정
INITIATION_TIMESTAMP = sys.argv[5] # Contact 시작 Timestamp
ASSOCIATED_CONTACTS = json.loads(sys.argv[6]) # 관련 Contacts
SEARCH_OPTION = sys.argv[7] # 선택 유형 
ENV = sys.argv[8]
FILE_PREFIX = f"{ENV}-main_flow_"

# Save Graph
def save_graph(dot, associated_contacts, output_file=f"{FILE_PREFIX}{SELECTED_CONTACT_ID}"):
    """
    Graphviz 그래프를 파일로 저장하고 DOT UI를 실행합니다.
    """
    fmt = "dot"
    file_path = f"./virtual_env/{output_file}"
    dot.render(file_path, format=fmt, cleanup=True)
    print(f"Contact 시각화가 {file_path}.{fmt} (으)로 저장되었습니다.")

    window = MainDotWindow(f"{file_path}.{fmt}", associated_contacts)
    window.connect('delete-event', Gtk.main_quit)
    Gtk.main()

def set_history_window(contact_id, associated_contacts):
    fmt = "dot"
    output_file = f"{FILE_PREFIX}{contact_id}"
    file_path = f"./virtual_env/{output_file}"
    window = MainDotWindow(f"{file_path}.{fmt}", associated_contacts)
    window.connect('delete-event', Gtk.main_quit)
    Gtk.main()

if __name__ == "__main__":

    if SEARCH_OPTION == "History":
        set_history_window(SELECTED_CONTACT_ID,ASSOCIATED_CONTACTS)
    else:
        dot = build_main_contacts(SELECTED_CONTACT_ID,ASSOCIATED_CONTACTS,INITIATION_TIMESTAMP,REGION,LOG_GROUP,ENV,INSTANCE_ID)

        save_graph(dot,ASSOCIATED_CONTACTS)
