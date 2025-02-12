import xdot.ui
import json
from gi.repository import Gtk


# Dot Window
class DotWindowBase(xdot.ui.DotWindow):
    """공통 DotWindow 로직을 포함한 기본 클래스"""
    
    def __init__(self, dot_file):
        super().__init__()
        self.dot_file = dot_file
        self.dotwidget.connect('clicked', self.on_node_clicked)
        self.open_file(self.dot_file)

    def on_delete_event(self, widget, event):
        print("Window closed")
        self.hide()
        return True

class MainDotWindow(DotWindowBase):
    """메인 Contact Flow 그래프를 표시하는 창"""

    def on_node_clicked(self, widget, sub_file, event):
        print(f"Opening sub flow: {sub_file}")
        SubDotWindow(sub_file)

class SubDotWindow(DotWindowBase):
    """서브 Contact Flow 그래프를 표시하는 창"""

    def on_node_clicked(self, widget, json_data, event):
        try:
            json_text = json.dumps(json_data, indent=4, ensure_ascii=False) if isinstance(json_data, dict) else json_data
            if json_text.startswith('./virtual_env/module_'):
                print(f"Opening sub flow: {json_data}")
                SubDotModuleWindow(json_data)
            else:
                print(f"Node clicked: \n{json_text}")

                dialog = Gtk.MessageDialog(parent=self, buttons=Gtk.ButtonsType.OK, message_format=json_text)
                dialog.connect('response', lambda dialog, response: dialog.destroy())
                dialog.run()
        except Exception as e:
            print(f"Error showing message dialog: {e}")

class SubDotModuleWindow(DotWindowBase):
    """서브 Contact Flow 그래프를 표시하는 창"""

    def on_node_clicked(self, widget, json_data, event):
        try:
            json_text = json.dumps(json_data, indent=4, ensure_ascii=False) if isinstance(json_data, dict) else json_data
            print(f"Node clicked: \n{json_text}")

            dialog = Gtk.MessageDialog(parent=self, buttons=Gtk.ButtonsType.OK, message_format=json_text)
            dialog.connect('response', lambda dialog, response: dialog.destroy())
            dialog.run()
        except Exception as e:
            print(f"Error showing message dialog: {e}")