"""
AWS Connect Contact Tracer Main Entry Point

이 스크립트는 AWS Connect의 Contact 흐름을 시각화하는 메인 진입점입니다.
"""
import json
import sys
import os
from typing import Dict, Any

# GUI 모드 감지 - DISPLAY 환경 변수와 --headless 플래그 확인
# USE_GUI = os.environ.get('DISPLAY') and '--headless' not in sys.argv

# if USE_GUI:
    # try:
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk
from xdot.ui.window import MainDotWindow
    # except (ImportError, ValueError) as e:
    #     print(f"Warning: GUI 모드를 사용할 수 없습니다: {e}")
    #     print("헤드리스 모드로 전환합니다.")
    #     USE_GUI = False

from dot_builder import build_main_contacts


# Command line arguments validation
def validate_args() -> Dict[str, Any]:
    """커맨드 라인 인자를 검증하고 파싱합니다."""
    # --headless 플래그 제거
    args = [arg for arg in sys.argv if arg != '--headless']

    if len(args) < 9:
        raise ValueError(
            "Usage: python main.py [--headless] <instance_name> <instance_id> <contact_id> "
            "<region> <timestamp> <contacts_json> <search_option> <env>"
        )

    return {
        'log_group': f'/aws/connect/{args[1]}',
        'instance_id': args[2],
        'contact_id': args[3],
        'region': args[4],
        'timestamp': args[5],
        'contacts': json.loads(args[6]),
        'search_option': args[7],
        'env': args[8],
        'file_prefix': f"{args[8]}-main_flow_"
    }


# Constants
OUTPUT_DIR = "./virtual_env"
OUTPUT_FORMAT = "dot"


def save_graph(dot, associated_contacts: Dict[str, Any], output_file: str) -> None:
    """
    Graphviz 그래프를 파일로 저장하고 DOT UI를 실행합니다 (GUI 모드인 경우).

    Args:
        dot: Graphviz Digraph 객체
        associated_contacts: 관련 Contact 정보
        output_file: 출력 파일명 (확장자 제외)
    """
    file_path = f"{OUTPUT_DIR}/{output_file}"

    # DOT 파일 저장
    dot.render(file_path, format=OUTPUT_FORMAT, cleanup=True)
    print(f"Contact 시각화가 {file_path}.{OUTPUT_FORMAT} (으)로 저장되었습니다.")

    # 웹에서 볼 수 있도록 SVG 파일도 생성
    try:
        dot.render(file_path, format='svg', cleanup=False)
        print(f"SVG 파일도 생성되었습니다: {file_path}.svg")
    except Exception as e:
        print(f"Warning: SVG 파일 생성 실패: {e}")

    # if USE_GUI:
    window = MainDotWindow(f"{file_path}.{OUTPUT_FORMAT}", associated_contacts)
    window.connect('delete-event', Gtk.main_quit)
    Gtk.main()
    # else:
    #     print(f"헤드리스 모드: GUI를 실행하지 않습니다. 파일은 {file_path}.{OUTPUT_FORMAT}에 저장되었습니다.")


def set_history_window(contact_id: str, associated_contacts: Dict[str, Any],
                       file_prefix: str) -> None:
    """
    이전 히스토리 파일을 열어 DOT UI를 실행합니다 (GUI 모드인 경우).

    Args:
        contact_id: Contact ID
        associated_contacts: 관련 Contact 정보
        file_prefix: 파일명 접두사
    """
    output_file = f"{file_prefix}{contact_id}"
    file_path = f"{OUTPUT_DIR}/{output_file}"


    window = MainDotWindow(f"{file_path}.{OUTPUT_FORMAT}", associated_contacts)
    window.connect('delete-event', Gtk.main_quit)
    Gtk.main()


def main() -> None:
    """메인 실행 함수"""
    try:
        args = validate_args()

        if args['search_option'] == "History":
            set_history_window(
                args['contact_id'],
                args['contacts'],
                args['file_prefix']
            )
        else:
            dot = build_main_contacts(
                args['contact_id'],
                args['contacts'],
                args['timestamp'],
                args['region'],
                args['log_group'],
                args['env'],
                args['instance_id']
            )

            output_file = f"{args['file_prefix']}{args['contact_id']}"
            save_graph(dot, args['contacts'], output_file)

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
