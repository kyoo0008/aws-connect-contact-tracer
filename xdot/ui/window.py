# Copyright 2008-2015 Jose Fonseca
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import math
import os
import re
import subprocess
import sys
import time
import operator

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('PangoCairo', '1.0')

from gi.repository import GLib
from gi.repository import GObject
from gi.repository import Gtk
from gi.repository import Gdk

from . import elements
import ast
import json
# See http://www.graphviz.org/pub/scm/graphviz-cairo/plugin/cairo/gvrender_cairo.c

# For pygtk inspiration and guidance see:
# - http://mirageiv.berlios.de/
# - http://comix.sourceforge.net/

from . import actions
from ..dot.lexer import ParseError
from ._xdotparser import XDotParser
from . import animation
from . import actions
from .elements import Graph


class DotWidget(Gtk.DrawingArea):
    """GTK widget that draws dot graphs."""

    # TODO GTK3: Second argument has to be of type Gdk.EventButton instead of object.
    __gsignals__ = {
        'clicked': (GObject.SignalFlags.RUN_LAST, None, (str, object)),
        'error': (GObject.SignalFlags.RUN_LAST, None, (str,)),
        'history': (GObject.SignalFlags.RUN_LAST, None, (bool, bool))
    }

    filter = 'dot'
    graphviz_version = None

    def __init__(self):
        Gtk.DrawingArea.__init__(self)

        self.graph = Graph()
        self.openfilename = None

        self.set_can_focus(True)

        self.connect("draw", self.on_draw)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                        Gdk.EventMask.BUTTON_RELEASE_MASK)
        self.connect("button-press-event", self.on_area_button_press)
        self.connect("button-release-event", self.on_area_button_release)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK |
                        Gdk.EventMask.POINTER_MOTION_HINT_MASK |
                        Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.SCROLL_MASK |
                        Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.connect("motion-notify-event", self.on_area_motion_notify)
        self.connect("scroll-event", self.on_area_scroll_event)
        self.connect("size-allocate", self.on_area_size_allocate)

        self.connect('key-press-event', self.on_key_press_event)
        self.last_mtime = None
        self.mtime_changed = False

        GLib.timeout_add(1000, self.update)

        self.x, self.y = 0.0, 0.0
        self.zoom_ratio = 1.0
        self.zoom_to_fit_on_resize = False
        self.animation = animation.NoAnimation(self)
        self.drag_action = actions.NullAction(self)
        self.presstime = None
        self.highlight = None
        self.highlight_search = False
        self.history_back = []
        self.history_forward = []

        self.zoom_gesture = Gtk.GestureZoom.new(self)
        self.zoom_gesture.connect("scale-changed", self.on_scale_changed)

    def error_dialog(self, message):
        self.emit('error', message)

    def set_filter(self, filter):
        self.filter = filter
        self.graphviz_version = None

    def run_filter(self, dotcode):
        if not self.filter:
            return dotcode
        try:
            p = subprocess.Popen(
                [self.filter, '-Txdot'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                universal_newlines=False
            )
        except OSError as exc:
            error = '%s: %s' % (self.filter, exc.strerror)
            p = subprocess.CalledProcessError(exc.errno, self.filter, exc.strerror)
        else:
            xdotcode, error = p.communicate(dotcode)
            error = error.decode()
        error = error.rstrip()
        if error:
            sys.stderr.write(error + '\n')
        if p.returncode != 0:
            self.error_dialog(error)
        return xdotcode

    def _set_dotcode(self, dotcode, filename=None, center=True):
        # By default DOT language is UTF-8, but it accepts other encodings
        assert isinstance(dotcode, bytes)
        xdotcode = self.run_filter(dotcode)
            
        if xdotcode is None:
            return False
        try:
            self.set_xdotcode(xdotcode, center=center)
        except ParseError as ex:
            self.error_dialog(str(ex))
            return False
        else:
            return True

    def set_dotcode(self, dotcode, filename=None, center=True):
        self.openfilename = None
        if self._set_dotcode(dotcode, filename, center=center):
            if filename is None:
                self.last_mtime = None
            else:
                self.last_mtime = os.stat(filename).st_mtime
            self.mtime_changed = False
            self.openfilename = filename
            return True

    def set_xdotcode(self, xdotcode, center=True):
        assert isinstance(xdotcode, bytes)

        if self.graphviz_version is None and self.filter is not None:
            stdout = subprocess.check_output([self.filter, '-V'], stderr=subprocess.STDOUT)
            stdout = stdout.rstrip()
            mo = re.match(br'^.* - .* version (?P<version>.*) \(.*\)$', stdout)
            assert mo
            self.graphviz_version = mo.group('version').decode('ascii')

        parser = XDotParser(xdotcode, graphviz_version=self.graphviz_version)
        self.graph = parser.parse()
        self.zoom_image(self.zoom_ratio, center=center)

    def reload(self):
        if self.openfilename is not None:
            try:
                fp = open(self.openfilename, 'rb')
                self._set_dotcode(fp.read(), self.openfilename, center=False)
                fp.close()
            except IOError:
                pass
            else:
                del self.history_back[:], self.history_forward[:]

    def update(self):
        if self.openfilename is not None:
            try:
                current_mtime = os.stat(self.openfilename).st_mtime
            except OSError:
                return True
            if current_mtime != self.last_mtime:
                self.last_mtime = current_mtime
                self.mtime_changed = True
            elif self.mtime_changed:
                self.mtime_changed = False
                self.reload()
        return True

    def _draw_graph(self, cr, rect):
        w, h = float(rect.width), float(rect.height)
        cx, cy = 0.5 * w, 0.5 * h
        x, y, ratio = self.x, self.y, self.zoom_ratio
        x0, y0 = x - cx / ratio, y - cy / ratio
        x1, y1 = x0 + w / ratio, y0 + h / ratio
        bounding = (x0, y0, x1, y1)

        cr.translate(cx, cy)
        cr.scale(ratio, ratio)
        cr.translate(-x, -y)
        self.graph.draw(cr, highlight_items=self.highlight, bounding=bounding)

    def on_draw(self, widget, cr):
        rect = self.get_allocation()
        Gtk.render_background(self.get_style_context(), cr, 0, 0,
                              rect.width, rect.height)

        cr.save()
        self._draw_graph(cr, rect)
        cr.restore()

        self.drag_action.draw(cr)

        return False

    def get_current_pos(self):
        return self.x, self.y

    def set_current_pos(self, x, y):
        self.x = x
        self.y = y
        self.queue_draw()

    def set_highlight(self, items, search=False):
        # Enable or disable search highlight
        if search:
            self.highlight_search = items is not None
        # Ignore cursor highlight while searching
        if self.highlight_search and not search:
            return
        if self.highlight != items:
            self.highlight = items
            self.queue_draw()

    def zoom_image(self, zoom_ratio, center=False, pos=None):
        # Constrain zoom ratio to a sane range to prevent numeric instability.
        zoom_ratio = min(zoom_ratio, 1E4)
        zoom_ratio = max(zoom_ratio, 1E-6)

        if center:
            self.x = self.graph.width/2
            self.y = self.graph.height/2
        elif pos is not None:
            rect = self.get_allocation()
            x, y = pos
            x -= 0.5*rect.width
            y -= 0.5*rect.height
            self.x += x / self.zoom_ratio - x / zoom_ratio
            self.y += y / self.zoom_ratio - y / zoom_ratio
        self.zoom_ratio = zoom_ratio
        self.zoom_to_fit_on_resize = False
        self.queue_draw()

    def zoom_to_area(self, x1, y1, x2, y2):
        rect = self.get_allocation()
        width = abs(x1 - x2)
        height = abs(y1 - y2)
        if width == 0 and height == 0:
            self.zoom_ratio *= self.ZOOM_INCREMENT
        else:
            self.zoom_ratio = min(
                float(rect.width)/float(width),
                float(rect.height)/float(height)
            )
        self.zoom_to_fit_on_resize = False
        self.x = (x1 + x2) / 2
        self.y = (y1 + y2) / 2
        self.queue_draw()

    def zoom_to_fit(self):
        rect = self.get_allocation()
        rect.x += self.ZOOM_TO_FIT_MARGIN
        rect.y += self.ZOOM_TO_FIT_MARGIN
        rect.width -= 2 * self.ZOOM_TO_FIT_MARGIN
        rect.height -= 2 * self.ZOOM_TO_FIT_MARGIN
        zoom_ratio = min(
            float(rect.width)/float(self.graph.width),
            float(rect.height)/float(self.graph.height)
        )
        self.zoom_image(zoom_ratio, center=True)
        self.zoom_to_fit_on_resize = True

    ZOOM_INCREMENT = 1.25
    ZOOM_TO_FIT_MARGIN = 12

    def on_zoom_in(self, action):
        self.zoom_image(self.zoom_ratio * self.ZOOM_INCREMENT)

    def on_zoom_out(self, action):
        self.zoom_image(self.zoom_ratio / self.ZOOM_INCREMENT)

    def on_zoom_fit(self, action):
        self.zoom_to_fit()

    def on_zoom_100(self, action):
        self.zoom_image(1.0)

    POS_INCREMENT = 100

    def on_key_press_event(self, widget, event):
        if event.keyval == Gdk.KEY_Left:
            self.x -= self.POS_INCREMENT/self.zoom_ratio
            self.queue_draw()
            return True
        if event.keyval == Gdk.KEY_Right:
            self.x += self.POS_INCREMENT/self.zoom_ratio
            self.queue_draw()
            return True
        if event.keyval == Gdk.KEY_Up:
            self.y -= self.POS_INCREMENT/self.zoom_ratio
            self.queue_draw()
            return True
        if event.keyval == Gdk.KEY_Down:
            self.y += self.POS_INCREMENT/self.zoom_ratio
            self.queue_draw()
            return True
        if event.keyval in (Gdk.KEY_Page_Up,
                            Gdk.KEY_plus,
                            Gdk.KEY_equal,
                            Gdk.KEY_KP_Add):
            self.zoom_image(self.zoom_ratio * self.ZOOM_INCREMENT)
            self.queue_draw()
            return True
        if event.keyval in (Gdk.KEY_Page_Down,
                            Gdk.KEY_minus,
                            Gdk.KEY_KP_Subtract):
            self.zoom_image(self.zoom_ratio / self.ZOOM_INCREMENT)
            self.queue_draw()
            return True
        if event.keyval == Gdk.KEY_Escape:
            self.drag_action.abort()
            self.drag_action = actions.NullAction(self)
            return True
        if event.keyval == Gdk.KEY_r:
            self.reload()
            return True
        if event.keyval == Gdk.KEY_f:
            win = widget.get_toplevel()
            find_toolitem = win.uimanager.get_widget('/ToolBar/Find')
            textentry = find_toolitem.get_children()
            win.set_focus(textentry[0])
            return True
        if event.keyval == Gdk.KEY_q:
            Gtk.main_quit()
            return True
        if event.keyval == Gdk.KEY_p:
            self.on_print()
            return True
        if event.keyval == Gdk.KEY_t:
            # toggle toolbar visibility
            win = widget.get_toplevel()
            toolbar = win.uimanager.get_widget("/ToolBar")
            toolbar.set_visible(not toolbar.get_visible())
            return True
        if event.keyval == Gdk.KEY_w:
            self.zoom_to_fit()
            return True
        return False

    print_settings = None

    def on_print(self, action=None):
        print_op = Gtk.PrintOperation()

        if self.print_settings is not None:
            print_op.set_print_settings(self.print_settings)

        print_op.connect("begin_print", self.begin_print)
        print_op.connect("draw_page", self.draw_page)

        res = print_op.run(Gtk.PrintOperationAction.PRINT_DIALOG, self.get_toplevel())
        if res == Gtk.PrintOperationResult.APPLY:
            self.print_settings = print_op.get_print_settings()

    def begin_print(self, operation, context):
        operation.set_n_pages(1)
        return True

    def draw_page(self, operation, context, page_nr):
        cr = context.get_cairo_context()
        rect = self.get_allocation()
        self._draw_graph(cr, rect)

    def get_drag_action(self, event):
        state = event.state
        if event.button in (1, 2):  # left or middle button
            modifiers = Gtk.accelerator_get_default_mod_mask()
            if state & modifiers == Gdk.ModifierType.CONTROL_MASK:
                return actions.ZoomAction
            elif state & modifiers == Gdk.ModifierType.SHIFT_MASK:
                return actions.ZoomAreaAction
            else:
                return actions.PanAction
        return actions.NullAction

    def on_area_button_press(self, area, event):
        self.animation.stop()
        self.drag_action.abort()
        action_type = self.get_drag_action(event)
        self.drag_action = action_type(self)
        self.drag_action.on_button_press(event)
        self.presstime = time.time()
        self.pressx = event.x
        self.pressy = event.y
        return False

    def is_click(self, event, click_fuzz=4, click_timeout=1.0):
        assert event.type == Gdk.EventType.BUTTON_RELEASE
        if self.presstime is None:
            # got a button release without seeing the press?
            return False
        # XXX instead of doing this complicated logic, shouldn't we listen
        # for gtk's clicked event instead?
        deltax = self.pressx - event.x
        deltay = self.pressy - event.y
        return (time.time() < self.presstime + click_timeout and
                math.hypot(deltax, deltay) < click_fuzz)

    def on_click(self, element, event):
        """Override this method in subclass to process
        click events. Note that element can be None
        (click on empty space)."""
        return False

    def on_area_button_release(self, area, event):
        self.drag_action.on_button_release(event)
        self.drag_action = actions.NullAction(self)
        x, y = int(event.x), int(event.y)
        if self.is_click(event):
            el = self.get_element(x, y)
            if self.on_click(el, event):
                return True

            if event.button == 1:
                url = self.get_url(x, y)
                if url is not None:
                    self.emit('clicked', url.url, event)
                else:
                    ctrl_held = event.state & Gdk.ModifierType.CONTROL_MASK
                    jump = self.get_jump(x, y, to_dst=ctrl_held)
                    if jump is not None:
                        self.animate_to(jump.x, jump.y)

                return True

        if event.button == 1 or event.button == 2:
            return True
        return False

    def on_area_scroll_event(self, area, event):
        if event.direction == Gdk.ScrollDirection.UP:
            self.zoom_image(self.zoom_ratio * self.ZOOM_INCREMENT,
                            pos=(event.x, event.y))
            return True
        elif event.direction == Gdk.ScrollDirection.DOWN:
            self.zoom_image(self.zoom_ratio / self.ZOOM_INCREMENT,
                            pos=(event.x, event.y))
        else:
            deltas = event.get_scroll_deltas()
            self.zoom_image(self.zoom_ratio * (1 - deltas.delta_y / 10),
                            pos=(event.x, event.y))
            return True
        return False

    def on_area_motion_notify(self, area, event):
        self.drag_action.on_motion_notify(event)
        return True

    def on_area_size_allocate(self, area, allocation):
        if self.zoom_to_fit_on_resize:
            self.zoom_to_fit()

    def on_scale_changed(self, gesture, scale):
        point, x, y = gesture.get_point()
        if point:
            pos = (x, y)
        new_zoom_ratio = self.zoom_ratio * math.exp(math.log(scale) / 3)
        self.zoom_image(new_zoom_ratio, pos=pos)

    def animate_to(self, x, y):
        del self.history_forward[:]
        self.history_back.append(self.get_current_pos())
        self.history_changed()
        self._animate_to(x, y)

    def _animate_to(self, x, y):
        self.animation = animation.ZoomToAnimation(self, x, y)
        self.animation.start()

    def history_changed(self):
        self.emit(
            'history',
            bool(self.history_back),
            bool(self.history_forward))

    def on_go_back(self, action=None):
        try:
            item = self.history_back.pop()
        except LookupError:
            return
        self.history_forward.append(self.get_current_pos())
        self.history_changed()
        self._animate_to(*item)

    def on_go_forward(self, action=None):
        try:
            item = self.history_forward.pop()
        except LookupError:
            return
        self.history_back.append(self.get_current_pos())
        self.history_changed()
        self._animate_to(*item)

    def window2graph(self, x, y):
        rect = self.get_allocation()
        x -= 0.5*rect.width
        y -= 0.5*rect.height
        x /= self.zoom_ratio
        y /= self.zoom_ratio
        x += self.x
        y += self.y
        return x, y

    def get_element(self, x, y):
        x, y = self.window2graph(x, y)
        return self.graph.get_element(x, y)

    def get_url(self, x, y):
        x, y = self.window2graph(x, y)
        return self.graph.get_url(x, y)

    def get_jump(self, x, y, to_dst = False):
        x, y = self.window2graph(x, y)
        return self.graph.get_jump(x, y, to_dst)

    def generate_subgraph_dot(self, subgraph):
        """서브그래프를 DOT 포맷으로 변환"""
        dot_lines = ["digraph G {"]
        
        for node in subgraph.nodes:
            dot_lines.append(f'"{node.id}" [label="{node.id.split("_")[0]}"];')
        
        for edge in subgraph.edges:
            dot_lines.append(f'"{edge.src.id}" -> "{edge.dst.id}";')

        dot_lines.append("}")
        return "\n".join(dot_lines).encode("utf-8")


class FindMenuToolAction(Gtk.Action):
    __gtype_name__ = "FindMenuToolAction"

    def do_create_tool_item(self):
        return Gtk.ToolItem()


class DotWindow(Gtk.Window):

    ui = '''
    <ui>
        <toolbar name="ToolBar">
            <toolitem action="Open"/>
            <toolitem action="Export"/>
            <toolitem action="Reload"/>
            <toolitem action="Print"/>
            <separator/>
            <toolitem action="Back"/>
            <toolitem action="Forward"/>
            <separator/>
            <toolitem action="ZoomIn"/>
            <toolitem action="ZoomOut"/>
            <toolitem action="ZoomFit"/>
            <toolitem action="Zoom100"/>
            <separator/>
            <toolitem name="FindDeep" action="FindDeep"/>
            <toolitem name="Find" action="Find"/>
            <separator name="FindNextSeparator"/>
            <toolitem action="FindNext"/>
            <separator name="FindStatusSeparator"/>
            <toolitem name="FindStatus" action="FindStatus"/>
        </toolbar>
    </ui>
    '''

    base_title = 'Dot Viewer'

    def __init__(self, widget=None, width=1200, height=1500):
        Gtk.Window.__init__(self)

        self.graph = Graph()

        window = self

        window.set_title(self.base_title)
        window.set_default_size(width, height)
        window.set_wmclass("xdot", "xdot")
        vbox = Gtk.VBox()
        window.add(vbox)

        self.dotwidget = widget or DotWidget()
        self.dotwidget.connect("error", lambda e, m: self.error_dialog(m))
        self.dotwidget.connect("history", self.on_history)

        # Create a UIManager instance
        uimanager = self.uimanager = Gtk.UIManager()

        # Add the accelerator group to the toplevel window
        accelgroup = uimanager.get_accel_group()
        window.add_accel_group(accelgroup)

        # Create an ActionGroup
        actiongroup = Gtk.ActionGroup('Actions')
        self.actiongroup = actiongroup

        

        # Create actions
        actiongroup.add_actions((
            ('Open', Gtk.STOCK_OPEN, None, None, "Open dot-file", self.on_open),
            ('Export', Gtk.STOCK_SAVE_AS, None, None, "Export graph to other format", self.on_export),
            ('Reload', Gtk.STOCK_REFRESH, None, None, "Reload graph", self.on_reload),
            ('Print', Gtk.STOCK_PRINT, None, None,
             "Prints the currently visible part of the graph", self.dotwidget.on_print),
            ('ZoomIn', Gtk.STOCK_ZOOM_IN, None, None, "Zoom in", self.dotwidget.on_zoom_in),
            ('ZoomOut', Gtk.STOCK_ZOOM_OUT, None, None, "Zoom out", self.dotwidget.on_zoom_out),
            ('ZoomFit', Gtk.STOCK_ZOOM_FIT, None, None, "Fit zoom", self.dotwidget.on_zoom_fit),
            ('Zoom100', Gtk.STOCK_ZOOM_100, None, None, "Reset zoom level", self.dotwidget.on_zoom_100),
            ('FindDeep', Gtk.STOCK_FIND_AND_REPLACE, 'Find Deeply', None, 'Find text deeply in all subflows', self.on_finddeep_search),
            ('FindNext', Gtk.STOCK_GO_FORWARD, 'Next Result', None, 'Move to the next search result', self.on_find_next),
        ))

        self.back_action = Gtk.Action('Back', None, None, Gtk.STOCK_GO_BACK)
        self.back_action.set_sensitive(False)
        self.back_action.connect("activate", self.dotwidget.on_go_back)
        actiongroup.add_action(self.back_action)

        self.forward_action = Gtk.Action('Forward', None, None, Gtk.STOCK_GO_FORWARD)
        self.forward_action.set_sensitive(False)
        self.forward_action.connect("activate", self.dotwidget.on_go_forward)
        actiongroup.add_action(self.forward_action)

        find_action = FindMenuToolAction("Find", None,
                                         "Find a node by name", None)
        actiongroup.add_action(find_action)

        findstatus_action = FindMenuToolAction("FindStatus", None,
                                               "Number of results found", None)
        actiongroup.add_action(findstatus_action)

        # finddeep_action
        self.finddeep_action = Gtk.Action('FindDeepSearch', 'Deep Search', 'Open File Search', None)
        # self.finddeep_action.connect("activate", self.on_finddeep_search)
        actiongroup.add_action(self.finddeep_action)

        

        # Add the actiongroup to the uimanager
        uimanager.insert_action_group(actiongroup, 0)

        # Add a UI descrption
        uimanager.add_ui_from_string(self.ui)

        # Create a Toolbar
        toolbar = uimanager.get_widget('/ToolBar')
        vbox.pack_start(toolbar, False, False, 0)

        vbox.pack_start(self.dotwidget, True, True, 0)

        self.last_open_dir = "."

        self.set_focus(self.dotwidget)

        # Add Find text search
        find_toolitem = uimanager.get_widget('/ToolBar/Find')
        self.textentry = Gtk.Entry()
        self.textentry.set_icon_from_stock(0, Gtk.STOCK_FIND)
        find_toolitem.add(self.textentry)

        self.textentry.set_activates_default(True)
        self.textentry.connect("activate", self.textentry_activate, self.textentry);
        self.textentry.connect("changed", self.textentry_changed, self.textentry);

        uimanager.get_widget('/ToolBar/FindNextSeparator').set_draw(False)
        uimanager.get_widget('/ToolBar/FindStatusSeparator').set_draw(False)
        self.find_next_toolitem = uimanager.get_widget('/ToolBar/FindNext')
        self.find_next_toolitem.set_sensitive(False)

        self.find_count = Gtk.Label()
        findstatus_toolitem = uimanager.get_widget('/ToolBar/FindStatus')
        findstatus_toolitem.add(self.find_count)

        self.show_all()

    def find_text(self, entry_text):
        found_items = []
        dot_widget = self.dotwidget
        try:
            regexp = re.compile(entry_text, re.IGNORECASE)
        except re.error as err:
            sys.stderr.write('warning: re.compile() failed with error "%s"\n' % err)
            return []
        for element in dot_widget.graph.nodes + dot_widget.graph.edges + dot_widget.graph.shapes:
            # if element.search_text(regexp):
            #     found_items.append(element)
            matched = element.search_text(regexp)

            if hasattr(element, 'url') and element.url:
                try:
                    url_text = json.dumps(ast.literal_eval(element.url), ensure_ascii=False)
                except Exception:
                    url_text = str(element.url)

                if regexp.search(url_text):
                    matched = True

            if matched:
                found_items.append(element)
            
        return sorted(found_items, key=operator.methodcaller('get_text'))

    def textentry_changed(self, widget, entry):
        self.find_count.set_label('')
        self.find_index = 0
        self.find_next_toolitem.set_sensitive(False)
        entry_text = entry.get_text()
        dot_widget = self.dotwidget
        if not entry_text:
            dot_widget.set_highlight(None, search=True)
            return

        found_items = self.find_text(entry_text)
        dot_widget.set_highlight(found_items, search=True)
        if found_items:
            self.find_count.set_label('%d nodes found' % len(found_items))

    def textentry_activate(self, widget, entry):
        self.find_index = 0
        self.find_next_toolitem.set_sensitive(False)
        entry_text = entry.get_text()
        dot_widget = self.dotwidget
        if not entry_text:
            dot_widget.set_highlight(None, search=True)
            self.set_focus(self.dotwidget)
            return

        found_items = self.find_text(entry_text)
        dot_widget.set_highlight(found_items, search=True)
        if found_items:
            dot_widget.animate_to(found_items[0].x, found_items[0].y)
        self.find_next_toolitem.set_sensitive(len(found_items) > 1)

    def set_filter(self, filter):
        self.dotwidget.set_filter(filter)

    def set_dotcode(self, dotcode, filename=None):
        if self.dotwidget.set_dotcode(dotcode, filename):
            self.update_title(filename)
            self.dotwidget.zoom_to_fit()

    def set_xdotcode(self, xdotcode, filename=None):
        if self.dotwidget.set_xdotcode(xdotcode):
            self.update_title(filename)
            self.dotwidget.zoom_to_fit()

    def update_title(self, filename=None):
        if filename is None:
            self.set_title(self.base_title)
        else:
            self.set_title(os.path.basename(filename) + ' - ' + self.base_title)

    def open_file(self, filename):
        try:
            fp = open(filename, 'rb')
            self.set_dotcode(fp.read(), filename)
            fp.close()
        except IOError as ex:
            self.error_dialog(str(ex))

    def on_open(self, action):
        chooser = Gtk.FileChooserDialog(parent=self,
                                        title="Open Graphviz File",
                                        action=Gtk.FileChooserAction.OPEN,
                                        buttons=(Gtk.STOCK_CANCEL,
                                                 Gtk.ResponseType.CANCEL,
                                                 Gtk.STOCK_OPEN,
                                                 Gtk.ResponseType.OK))
        chooser.set_default_response(Gtk.ResponseType.OK)
        chooser.set_current_folder(self.last_open_dir)
        filter = Gtk.FileFilter()
        filter.set_name("Graphviz files")
        filter.add_pattern("*.gv")
        filter.add_pattern("*.dot")
        chooser.add_filter(filter)
        filter = Gtk.FileFilter()
        filter.set_name("All files")
        filter.add_pattern("*")
        chooser.add_filter(filter)
        if chooser.run() == Gtk.ResponseType.OK:
            filename = chooser.get_filename()
            self.last_open_dir = chooser.get_current_folder()
            chooser.destroy()
            self.open_file(filename)
        else:
            chooser.destroy()
   
    def export_file(self, filename, format_):
        if not filename.endswith("." + format_):
            filename += '.' + format_
        cmd = [
            self.dotwidget.filter, # program name, usually "dot"
            '-T' + format_,
            '-o', filename,
            self.dotwidget.openfilename,
        ]
        subprocess.check_call(cmd)

    def on_export(self, action):
        
        if self.dotwidget.openfilename is None:
            return
        
        default_filter = "PNG image"
    
        output_formats = {
            "dot file": "dot",
            "GIF image": "gif",
            "JPG image": "jpg",
            "JSON": "json",
            "PDF": "pdf",
            "PNG image": "png",
            "PostScript": "ps",
            "SVG image": "svg",
            "XFIG image": "fig",
            "xdot file": "xdot",
        }
        buttons = (
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        chooser = Gtk.FileChooserDialog(
            parent=self,
            title="Export to other file format.",
            action=Gtk.FileChooserAction.SAVE,
            buttons=buttons) 
        chooser.set_default_response(Gtk.ResponseType.OK)
        chooser.set_current_folder(self.last_open_dir)
        
        openfilename = os.path.basename(self.dotwidget.openfilename)
        openfileroot = os.path.splitext(openfilename)[0]
        chooser.set_current_name(openfileroot)

        for name, ext in output_formats.items():
            filter_ = Gtk.FileFilter()
            filter_.set_name(name)
            filter_.add_pattern('*.' + ext)
            chooser.add_filter(filter_)
            if name == default_filter:
                chooser.set_filter(filter_)

        if chooser.run() == Gtk.ResponseType.OK:
            filename = chooser.get_filename()
            format_ = output_formats[chooser.get_filter().get_name()]
            chooser.destroy()
            self.export_file(filename, format_)
        else:
            chooser.destroy()
	

    def on_reload(self, action):
        self.dotwidget.reload()

    def error_dialog(self, message):
        dlg = Gtk.MessageDialog(parent=self,
                                type=Gtk.MessageType.ERROR,
                                message_format=message,
                                buttons=Gtk.ButtonsType.OK)
        dlg.set_title(self.base_title)
        dlg.run()
        dlg.destroy()

    def on_find_next(self, action):
        self.find_index += 1
        entry_text = self.textentry.get_text()
        # Maybe storing the search result would be better
        found_items = self.find_text(entry_text)
        found_item = found_items[self.find_index]
        self.dotwidget.animate_to(found_item.x, found_item.y)
        self.find_next_toolitem.set_sensitive(len(found_items) > self.find_index + 1)

    def on_history(self, action, has_back, has_forward):
        self.back_action.set_sensitive(has_back)
        self.forward_action.set_sensitive(has_forward)

    def on_finddeep_search(self, action):

        
        dialog = SearchDialog(self)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            search_text = dialog.entry.get_text()
            # self.search_files(search_text)
            search_window = TextViewWindow(search_text, self.associated_contacts)
            search_window.show_all()
        dialog.destroy()

    def set_contact_ids(self, associated_contacts):
        self.associated_contact_ids = [contact['ContactId'] for contact in associated_contacts['ContactSummaryList']]



class SearchDialog(Gtk.Dialog):
    def __init__(self, parent):
        super().__init__(title="Search", transient_for=parent, modal=True)
        self.add_buttons(
            Gtk.STOCK_FIND,
            Gtk.ResponseType.OK,
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
        )

        box = self.get_content_area()

        label = Gtk.Label(label="Insert text you want to search for:")
        box.add(label)

        self.entry = Gtk.Entry()
        box.add(self.entry)

        self.show_all()



class TextViewWindow(Gtk.Window):
    def __init__(self, search_text, associated_contacts):
        Gtk.Window.__init__(self, title="File Search Example")

        self.associated_contacts = associated_contacts
        self.set_default_size(-1, 350)

        self.grid = Gtk.Grid()
        self.add(self.grid)

        self.create_textview()

        self.search_files(search_text, associated_contacts)

    def create_textview(self):
        scrolledwindow = Gtk.ScrolledWindow()
        scrolledwindow.set_hexpand(True)
        scrolledwindow.set_vexpand(True)
        self.grid.attach(scrolledwindow, 0, 1, 3, 1)

        self.textview = Gtk.TextView()
        self.textbuffer = self.textview.get_buffer()
        self.textbuffer.set_text("Search results will appear here.")
        scrolledwindow.add(self.textview)

        # ✅ 파일 클릭 시 이벤트 처리용 ListBox 추가
        self.listbox = Gtk.ListBox()
        self.grid.attach(self.listbox, 0, 2, 3, 1)

    def search_files(self, keyword, associated_contacts):
        directory = "./virtual_env/"
        result_files = []
        associated_contact_ids = [contact['ContactId'] for contact in associated_contacts['ContactSummaryList']]
        for filename in os.listdir(directory):
            if filename == ".DS_Store":
                continue
            file_path = os.path.join(directory, filename)

            if os.path.isfile(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        if keyword in content and filename.endswith(".dot") and "xray" not in filename and "main_flow" not in filename:
                            for associated_contact_id in associated_contact_ids:
                                if associated_contact_id in content:
                                    result_files.append({associated_contact_id:filename})
                except Exception as e:
                    print(f"Error reading {filename}: {e}")

        if result_files:
            result_text = f"🔍 Search Results: {len(result_files)} founds\n\n"
            #     for file_dict in result_files:
            #         for contact_id, filename in file_dict.items():
            #             result_text += f"{contact_id} → {filename}\n"

            self.populate_listbox(result_files, keyword)
        else:
            result_text = "❌ No files contain the keyword."

        self.textbuffer.set_text(result_text)

    def populate_listbox(self, file_list, keyword):
        for file_dict in file_list:
            for contact_id, filename in file_dict.items():
                row = Gtk.ListBoxRow()
                display_name = ""
                
                if len(filename.split("__")) > 0:
                    if filename.startswith("module"):
                        display_name = filename.split("__")[1] + " >> " + filename.split("__")[2]
                    else:
                        display_name = filename.split("__")[1]
                button = Gtk.Button(label=f"🆔  :  {contact_id} → {display_name.replace(".dot","")}")
                button.connect("clicked", self.on_file_selected, contact_id, filename, keyword)
                row.add(button)
                self.listbox.add(row)


        self.listbox.show_all()

    def on_file_selected(self, button, contact_id, filename, keyword):

        if "-main_flow_" in filename:
            MainDotWindow(f'./virtual_env/{filename}', self.associated_contacts, keyword)
        else:
            SubDotWindow(f'./virtual_env/{filename}', self.associated_contacts, keyword)

        # self.destroy()





class DotWindowBase(DotWindow):
    """공통 DotWindow 로직을 포함한 기본 클래스"""
    
    def __init__(self, dot_file, associated_contacts, keyword=None):

        super().__init__()
        self.dot_file = dot_file
        self.associated_contacts = associated_contacts
        self.default_keyword = keyword
        

        self.dotwidget.connect('clicked', self.on_node_clicked)
        self.open_file(self.dot_file)
        # self.set_contact_ids(self.associated_contacts)
        if self.default_keyword:
            self.textentry.set_text(self.default_keyword)
            self.find_text(self.default_keyword)


    def on_delete_event(self, widget, event):
        print("창이 닫혔습니다.")
        self.hide()
        return True




class MainDotWindow(DotWindowBase):
    """메인 Contact Flow 그래프를 표시하는 창"""

    def __init__(self, dot_file, associated_contacts, keyword=None):
        super().__init__(dot_file, associated_contacts, keyword)
        self.associated_contacts = associated_contacts
        self.default_keyword = keyword

        
    def on_node_clicked(self, widget, sub_file, event):
        
        if ("flow" in sub_file and ".dot" in sub_file) or "transcript" in sub_file or "lex" in sub_file:
            print(f"서브 플로우 열기: {sub_file}")
            SubDotWindow(sub_file, self.associated_contacts)
        else:
            if isinstance(sub_file, dict):
                json_text = json.dumps(sub_file, indent=4, ensure_ascii=False) 
                print(f"노드 클릭됨: \n{json_text}")
                TextViewDialog("노드 정보", json_text)
            else: # contact attributes
                json_text = ast.literal_eval(sub_file)
                # print(f"노드 클릭됨: \n{json_text}")
                AttributeTable(json_text)



class SubDotWindow(DotWindowBase):
    """서브 Contact Flow 그래프를 표시하는 창"""
    def __init__(self, dot_file, associated_contacts, keyword=None):
        super().__init__(dot_file, associated_contacts, keyword)
        self.associated_contacts = associated_contacts
        self.default_keyword = keyword

    def on_node_clicked(self, widget, json_data, event):
        try:
            json_text = json.dumps(json_data, indent=4, ensure_ascii=False) if isinstance(json_data, dict) else json_data
            if json_text.startswith('./virtual_env/module_'):
                print(f"서브 플로우 열기: {json_data}")
                SubDotModuleWindow(json_data, self.associated_contacts)
            elif json_text.startswith('./virtual_env/xray'):
                print(f"서브 플로우 열기: {json_data}")
                SubDotXrayWindow(json_data, self.associated_contacts)
            elif json_text.startswith('./virtual_env/transcript') or json_text.startswith('./virtual_env/lex'):
                print(f"서브 플로우 열기: {json_data}")
                SubDotTranscriptWindow(json_data, self.associated_contacts)
            else:
                print(f"노드 클릭됨: \n{json_text}")
                TextViewDialog("노드 정보", json_text)
        except Exception as e:
            print(f"SubDotWindow 표시 오류: {e}")


class SubDotModuleWindow(DotWindowBase):
    """모듈 서브 Contact Flow 그래프를 표시하는 창"""
    def __init__(self, dot_file, associated_contacts, keyword=None):
        super().__init__(dot_file, associated_contacts, keyword)
        self.associated_contacts = associated_contacts
        self.default_keyword = keyword

    def on_node_clicked(self, widget, json_data, event):
        try:
            json_text = json.dumps(json_data, indent=4, ensure_ascii=False) if isinstance(json_data, dict) else json_data
            if json_text.startswith('./virtual_env/xray'):
                print(f"서브 플로우 열기: {json_data}")
                SubDotXrayWindow(json_data,self.associated_contacts)
            else:
                print(f"노드 클릭됨: \n{json_text}")
                TextViewDialog("노드 정보", json_text)
        except Exception as e:
            print(f"SubDotModuleWindow 표시 오류: {e}")


class SubDotXrayWindow(DotWindowBase):
    """X-Ray 서브 Contact Flow 그래프를 표시하는 창"""
    def __init__(self, dot_file, associated_contacts, keyword=None):
        super().__init__(dot_file, associated_contacts, keyword)
        self.associated_contacts = associated_contacts
        self.default_keyword = keyword

    def on_node_clicked(self, widget, json_data, event):
        try:
            json_text = json.dumps(json_data, indent=4, ensure_ascii=False) if isinstance(json_data, dict) else json_data
            print(f"노드 클릭됨: \n{json_text}")    
            TextViewDialog("노드 정보", json_text)
        except Exception as e:
            print(f"SubDotXrayWindow 표시 오류: {e}")


class SubDotTranscriptWindow(DotWindowBase):
    """Contact Transcript를 표시하는 창"""
    def __init__(self, dot_file, associated_contacts, keyword=None):
        super().__init__(dot_file, associated_contacts, keyword)
        self.associated_contacts = associated_contacts
        self.default_keyword = keyword

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