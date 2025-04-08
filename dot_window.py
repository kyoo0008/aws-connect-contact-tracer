import xdot.ui
import json
from gi.repository import Gtk


class DotWindowBase(xdot.ui.DotWindow):
    """공통 DotWindow 로직을 포함한 기본 클래스"""
    
    def __init__(self, dot_file):
        super().__init__()
        self.dot_file = dot_file
        self.dotwidget.connect('clicked', self.on_node_clicked)
        self.open_file(self.dot_file)

    def on_delete_event(self, widget, event):
        print("창이 닫혔습니다.")
        self.hide()
        return True


class TextViewDialog(Gtk.Window):
    """스크롤 가능한 텍스트 뷰어 창"""

    def __init__(self, title, text):
        super().__init__(title=title)
        self.set_default_size(500, 800)
        self.set_position(Gtk.WindowPosition.CENTER)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_border_width(10)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        
        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        text_buffer = text_view.get_buffer()
        text_buffer.set_text(text)

        scrolled_window.add(text_view)
        vbox.pack_start(scrolled_window, True, True, 0)

        self.add(vbox)
        self.show_all()


class MainDotWindow(DotWindowBase):
    """메인 Contact Flow 그래프를 표시하는 창"""

    def on_node_clicked(self, widget, sub_file, event):
        
        if ("flow" in sub_file and ".dot" in sub_file) or "transcript" in sub_file or "lex" in sub_file:
            print(f"서브 플로우 열기: {sub_file}")
            SubDotWindow(sub_file)
        else:
            json_text = json.dumps(sub_file, indent=4, ensure_ascii=False) if isinstance(sub_file, dict) else sub_file
            print(f"노드 클릭됨: \n{json_text}")
            TextViewDialog("노드 정보", json_text)



class SubDotWindow(DotWindowBase):
    """서브 Contact Flow 그래프를 표시하는 창"""

    def on_node_clicked(self, widget, json_data, event):
        try:
            json_text = json.dumps(json_data, indent=4, ensure_ascii=False) if isinstance(json_data, dict) else json_data
            if json_text.startswith('./virtual_env/module_'):
                print(f"서브 플로우 열기: {json_data}")
                SubDotModuleWindow(json_data)
            elif json_text.startswith('./virtual_env/xray'):
                print(f"서브 플로우 열기: {json_data}")
                SubDotXrayWindow(json_data)
            elif json_text.startswith('./virtual_env/transcript') or json_text.startswith('./virtual_env/lex'):
                print(f"서브 플로우 열기: {json_data}")
                SubDotTranscriptWindow(json_data)
            else:
                print(f"노드 클릭됨: \n{json_text}")
                TextViewDialog("노드 정보", json_text)
        except Exception as e:
            print(f"SubDotWindow 표시 오류: {e}")


class SubDotModuleWindow(DotWindowBase):
    """모듈 서브 Contact Flow 그래프를 표시하는 창"""

    def on_node_clicked(self, widget, json_data, event):
        try:
            json_text = json.dumps(json_data, indent=4, ensure_ascii=False) if isinstance(json_data, dict) else json_data
            if json_text.startswith('./virtual_env/xray'):
                print(f"서브 플로우 열기: {json_data}")
                SubDotXrayWindow(json_data)
            else:
                print(f"노드 클릭됨: \n{json_text}")
                TextViewDialog("노드 정보", json_text)
        except Exception as e:
            print(f"SubDotModuleWindow 표시 오류: {e}")


class SubDotXrayWindow(DotWindowBase):
    """X-Ray 서브 Contact Flow 그래프를 표시하는 창"""

    def on_node_clicked(self, widget, json_data, event):
        try:
            json_text = json.dumps(json_data, indent=4, ensure_ascii=False) if isinstance(json_data, dict) else json_data
            print(f"노드 클릭됨: \n{json_text}")    
            TextViewDialog("노드 정보", json_text)
        except Exception as e:
            print(f"SubDotXrayWindow 표시 오류: {e}")


class SubDotTranscriptWindow(DotWindowBase):
    """Contact Transcript를 표시하는 창"""

    def on_node_clicked(self, widget, json_data, event):
        try:
            json_text = json.dumps(json_data, indent=4, ensure_ascii=False) if isinstance(json_data, dict) else json_data
            print(f"노드 클릭됨: \n{json_text}")    
            TextViewDialog("노드 정보", json_text)
        except Exception as e:
            print(f"SubDotXrayWindow 표시 오류: {e}")