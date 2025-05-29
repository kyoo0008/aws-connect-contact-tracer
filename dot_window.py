import xdot.ui
import json
from gi.repository import Gtk
import ast

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
        self.set_default_size(700, 800)
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
            if isinstance(sub_file, dict):
                json_text = json.dumps(sub_file, indent=4, ensure_ascii=False) 
                print(f"노드 클릭됨: \n{json_text}")
                TextViewDialog("노드 정보", json_text)
            else:
                json_text = ast.literal_eval(sub_file)
                # print(f"노드 클릭됨: \n{json_text}")
                AttributeTable(json_text)



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


class AttributeTable(Gtk.Window):
    def __init__(self, data):
        Gtk.Window.__init__(self, title="Contact Attributes")
        self.set_default_size(1200, 900)
        self.set_border_width(10)


        # Create a ListStore with 4 string columns
        self.store = Gtk.ListStore(str, str, str, str)
        for item in data:
            self.store.append([
                item["k"],
                item["v"],
                item["c"],
                item["i"]
            ])

        
        sorted_model = Gtk.TreeModelSort(model=self.store)
        sorted_model.set_sort_column_id(0, Gtk.SortType.ASCENDING)

        # Create the TreeView using the store

        treeview = Gtk.TreeView(model=sorted_model)
        columns = ["Key", "Value", "Contact Flow", "Identifier"]
        for i, column_title in enumerate(columns):
            renderer = Gtk.CellRendererText()
            # renderer.set_property("wrap-mode", Gtk.WrapMode.WORD_CHAR)
            renderer.set_property("wrap-width", 400 if column_title == "Value" else 200)
            
            column = Gtk.TreeViewColumn(column_title, renderer, text=i)
            column.set_resizable(True)
            column.set_min_width(400 if column_title == "Value" else 150)
            treeview.append_column(column)

        # 스크롤 가능하게 감싸고 테두리도 추가
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame.set_label("📋 Contact Attribute Details")
        frame.set_label_align(0.5, 0.5)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.add(treeview)

        frame.add(scrolled_window)
        self.add(frame)
        self.show_all()