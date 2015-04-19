from burp import IBurpExtender, ITab, IHttpListener, IMessageEditorController, IContextMenuFactory, IScanIssue, IHttpService, IHttpRequestResponse
from java.awt import Component
from java.awt.event import ActionListener
from java.io import PrintWriter
from java.util import ArrayList, List
from java.net import URL
from javax.swing import JScrollPane, JSplitPane, JTabbedPane, JTable, SwingUtilities, JPanel, JButton, JLabel, JMenuItem
from javax.swing.table import AbstractTableModel
from threading import Lock
import datetime, os, hashlib
import sys


'''
Entry point for Burp Chorus extension.
'''

class BurpExtender(IBurpExtender, IHttpListener):
    '''
    Entry point for plugin; creates UI, and Log
    Will create GitRepo and (probably) a standalone InputHandler later
    '''
    
    def	registerExtenderCallbacks(self, callbacks):
        sys.stdout = callbacks.getStdout()
        sys.stderr = callbacks.getStderr()
    
        self._callbacks = callbacks
        self._helpers = callbacks.getHelpers()
        callbacks.setExtensionName("Burp Chorus")
        
        self.log = Log(callbacks)
        self.ui = BurpUi(callbacks, self.log)
        self.log.setUi(self.ui)
       
        callbacks.registerHttpListener(self)

    def processHttpMessage(self, toolFlag, messageIsRequest, messageInfo):
        pass
        #if not messageIsRequest:
        #    self.log.add_network_entry(toolFlag, messageInfo)
       

'''
Classes that support logging of data to in-Burp extension UI as well
as the underlying git repo
'''

class LogEntry(object):
    def __init__(self, *args, **kwargs):
        self.__dict__ = kwargs

        md5 = hashlib.md5()
        for k, v in self.__dict__.iteritems():
            if v and k != "messages": 
                if not getattr(v, "__getitem__", False):
                    v = str(v)
                md5.update(k)
                md5.update(v[:2048])
        self.md5 = md5



class Log():
    '''
    Log of burp activity: commands handles both the Burp UI log and the git 
    repo log.
    Used by BurpExtender when it logs input events.
    '''

    def __init__(self, callbacks):
        self.ui = None
        self._callbacks = callbacks
        self._helpers = callbacks.getHelpers()
        self.gui_log = GuiLog(callbacks)
        self.git_log = GitLog(callbacks)

    def setUi(self, ui):
        self.ui = ui
        self.gui_log.ui = ui

    def reload(self):
        self.gui_log.clear() 
        for entry in self.git_log.entries():
            self.gui_log.add_entry(entry)

    def add_repeater_entry(self, messageInfo):
        '''
        Grab salient info from Burp and store it to GUI and Git logs
        '''

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        service = messageInfo.getHttpService() 
        entry = LogEntry(tool="repeater",
                host=service.getHost(), 
                port=service.getPort(), 
                protocol=service.getProtocol(), 
                url=str(self._helpers.analyzeRequest(messageInfo).getUrl()), 
                timestamp=timestamp,
                who=self.git_log.whoami(),
                request=messageInfo.getRequest(),
                response=messageInfo.getResponse())
        self.gui_log.add_entry(entry)
        self.git_log.add_repeater_entry(entry)

    def add_scanner_entry(self, scanIssue):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Gather info from messages. Oi, should probably re-design this.

        messages = []
        for message in scanIssue.getHttpMessages():
            service = message.getHttpService() 
            msg_entry = LogEntry(tool="scanner_message",
                    host=service.getHost(), 
                    port=service.getPort(), 
                    protocol=service.getProtocol(), 
                    comment=message.getComment(),
                    highlight=message.getHighlight(),
                    request=message.getRequest(),
                    response=message.getResponse(),
                    timestamp=timestamp)
            messages.append(msg_entry)


        # Gather info for scan issue

        service = scanIssue.getHttpService() 
        entry = LogEntry(tool="scanner",
                timestamp=timestamp,
                who=self.git_log.whoami(),
                messages=messages,
                host=service.getHost(), 
                port=service.getPort(), 
                protocol=service.getProtocol(), 
                confidence=scanIssue.getConfidence(),
                issue_background=scanIssue.getIssueBackground(),
                issue_detail=scanIssue.getIssueDetail(),
                issue_name=scanIssue.getIssueName(),
                issue_type=scanIssue.getIssueType(),
                remediation_background=scanIssue.getRemediationBackground(),
                remediation_detail=scanIssue.getRemediationDetail(),
                severity=scanIssue.getSeverity(),
                url=str(scanIssue.getUrl()))

        self.gui_log.add_entry(entry)
        self.git_log.add_scanner_entry(entry)

class GuiLog(AbstractTableModel):
    '''
    Log of burp activity: commands handles both the Burp UI log and the git 
    repo log.
    Acts as a AbstractTableModel for that table that is show in the UI tab. 
    '''

    def __init__(self, callbacks):
        self.ui = None
        self._log = ArrayList()
        self._lock = Lock()
        self._callbacks = callbacks
        self._helpers = callbacks.getHelpers()

    def clear(self):
        self._lock.acquire()
        last = self._log.size()
        if last > 0:
            self._log.clear()
            self.fireTableRowsDeleted(0, last-1)
        # Note: if callees modify table this could deadlock
        self._lock.release()

    def add_entry(self, entry):

        self._lock.acquire()
        row = self._log.size()
        self._log.add(entry)
        # Note: if callees modify table this could deadlock
        self.fireTableRowsInserted(row, row)
        self._lock.release()

    def getRowCount(self):
        try:
            return self._log.size()
        except:
            return 0
    
    def getColumnCount(self):
        return 4
    
    def getColumnName(self, columnIndex):
        cols = ["Time added", 
                "Tool",
                "URL",
                "Issue",
                "Who"]
        try:
            return cols[columnIndex]
        except KeyError:
            return ""

    def get(self, rowIndex):
        return self._log.get(rowIndex)
    
    def getValueAt(self, rowIndex, columnIndex):
        logEntry = self._log.get(rowIndex)
        if columnIndex == 0:
            return logEntry.timestamp
        elif columnIndex == 1:
            return logEntry.tool.capitalize()
        elif columnIndex == 2:
            return logEntry.url
        elif columnIndex == 3:
            if logEntry.tool == "scanner":
                return logEntry.issue_name
            else:
                return "N/A"
        elif columnIndex == 4:
            return logEntry.who

        return ""

import os, subprocess
class GitLog(object):
    def __init__(self, callbacks):

        self.callbacks = callbacks

        # Set directory paths and if necessary, init git repo

        home = os.path.expanduser("~")
        self.repo_path = os.path.join(home, ".burp-chorus")

        if not os.path.exists(self.repo_path):
            subprocess.check_call(["git", "init", self.repo_path], cwd=home)

    def add_repeater_entry(self, entry):

        # Make directory for this entry

        entry_dir = os.path.join(self.repo_path, entry.md5.hexdigest())
        if not os.path.exists(entry_dir):
            os.mkdir(entry_dir)
        
        # Add and commit repeater data to git repo

        self.write_entry(entry, entry_dir)
        subprocess.check_call(["git", "commit", "-m", "Added Repeater entry"], 
                cwd=self.repo_path)

    def write_entry(self, entry, entry_dir):
        '''
        Stores entry to entry_dir and adds it to git repo
        '''
        if not os.path.exists(entry_dir):
            os.mkdir(entry_dir)
        for filename, data in entry.__dict__.iteritems():
            if not data:
                data = ""
            if not getattr(data, "__getitem__", False):
                data = str(data)
            path = os.path.join(entry_dir, filename)
            with open(path, "wb") as fp:
                fp.write(data)
                fp.flush()
                fp.close()
            subprocess.check_call(["git", "add", path], 
                    cwd=self.repo_path)


    def add_scanner_entry(self, entry):

        # Create dir hierarchy for this issue

        entry_dir = os.path.join(self.repo_path, entry.md5.hexdigest())


        # Log this entry; log 'messages' to its own subdir 

        messages = entry.messages
        del entry.__dict__["messages"]
        self.write_entry(entry, entry_dir)
        messages_dir = os.path.join(entry_dir, "messages")
        if not os.path.exists(messages_dir):
            os.mkdir(messages_dir)
            open(os.path.join(messages_dir, ".chorus-list"), "wt")
        i = 0
        for message in messages:
            message_dir = os.path.join(messages_dir, str(i))
            if not os.path.exists(message_dir):
                os.mkdir(message_dir)
            self.write_entry(message, message_dir)
            i += 1

        subprocess.check_call(["git", "commit", "-m", "Added scanner entry"], 
                cwd=self.repo_path)


    def entries(self):
        '''
        Generator; yields each entry in repo
        '''
        def load_entry(entry_path):
            filenames = os.listdir(entry_path)
            if ".chorus-list" in filenames:
                return load_list(entry_path)
            entry = LogEntry()
            for filename in filenames:
                file_path = os.path.join(entry_path, filename)
                if os.path.isdir(file_path):
                    sub_entry = load_entry(file_path)
                    entry.__dict__[filename] = sub_entry
                else:
                    entry.__dict__[filename] = open(file_path, "rb").read()
            return entry

        def load_list(entry_path):
            entries = []
            for filename in os.listdir(entry_path):
                file_path = os.path.join(entry_path, filename)
                if filename == ".chorus-list":
                    continue
                entries.append(load_entry(file_path))
            return entries

        for entry_dir in os.listdir(self.repo_path):
            if entry_dir == ".git":
                continue
            entry_path = os.path.join(self.repo_path, entry_dir)
            if not os.path.isdir(entry_path):
                continue
            entry = load_entry(entry_path)
            yield entry

    def whoami(self):
        return subprocess.check_output(["git", "config", "user.name"], 
                cwd=self.repo_path)



'''
Implementation of extension's UI.
'''

class BurpUi(ITab):
    '''
    The collection of objects that make up this extension's Burp UI. Created
    by BurpExtender.
    '''

    def __init__(self, callbacks, log):

        # Create split pane with top and bottom panes

        self._splitpane = JSplitPane(JSplitPane.VERTICAL_SPLIT)
        self.bottom_pane = UiBottomPane(callbacks)
        self.top_pane = UiTopPane(callbacks, self.bottom_pane, log)
        self.bottom_pane.setLogTable(self.top_pane.logTable)
        self._splitpane.setLeftComponent(self.top_pane)
        self._splitpane.setRightComponent(self.bottom_pane)


        # Create right-click handler

        self.log = log
        rc_handler = RightClickHandler(callbacks, log)
        callbacks.registerContextMenuFactory(rc_handler)

        
        # Add the plugin's custom tab to Burp's UI

        callbacks.customizeUiComponent(self._splitpane)
        callbacks.addSuiteTab(self)

      
    def getTabCaption(self):
        return "Chorus"
       
    def getUiComponent(self):
        return self._splitpane

class RightClickHandler(IContextMenuFactory):
    def __init__(self, callbacks, log):
        self.callbacks = callbacks
        self.log = log

    def createMenuItems(self, invocation):
        import sys
        sys.stdout.write("invoked\n")
        context = invocation.getInvocationContext()
        tool = invocation.getToolFlag()
        if tool == self.callbacks.TOOL_REPEATER:
            if context in [invocation.CONTEXT_MESSAGE_EDITOR_REQUEST, invocation.CONTEXT_MESSAGE_VIEWER_RESPONSE]:
                item = JMenuItem("Send to Chorus")
                item.addActionListener(self.RepeaterHandler(self.callbacks, invocation, self.log))
                items = ArrayList()
                items.add(item)
                return items
        elif tool == self.callbacks.TOOL_SCANNER:
            if context in [invocation.CONTEXT_SCANNER_RESULTS]:
                item = JMenuItem("Send to Chorus")
                item.addActionListener(self.ScannerHandler(self.callbacks, invocation, self.log))
                items = ArrayList()
                items.add(item)
                return items
        else:
            # TODO: add support for other tools
            pass

    class ScannerHandler(ActionListener):
        def __init__(self, callbacks, invocation, log):
            self.callbacks = callbacks
            self.invocation = invocation
            self.log = log

        def actionPerformed(self, actionEvent):
            for issue in self.invocation.getSelectedIssues():
                self.log.add_scanner_entry(issue) 

    class RepeaterHandler(ActionListener):
        def __init__(self, callbacks, invocation, log):
            self.callbacks = callbacks
            self.invocation = invocation
            self.log = log

        def actionPerformed(self, actionEvent):
            for message in self.invocation.getSelectedMessages():
                self.log.add_repeater_entry(message) 

class UiBottomPane(JTabbedPane, IMessageEditorController):
    '''
    The bottom pane in the this extension's UI tab. It shows detail of 
    whatever is selected in the top pane.
    '''
    def __init__(self, callbacks):
        self.sendPanel = SendPanel(callbacks)
        self._requestViewer = callbacks.createMessageEditor(self, False)
        self._responseViewer = callbacks.createMessageEditor(self, False)
        callbacks.customizeUiComponent(self)

    def setLogTable(self, log_table):
        '''
        Passes the Log table to the "Send to Tools" component so it can grab
        the selected rows
        '''
        self.sendPanel.log_table = log_table

    def show_log_entry(self, log_entry):
        '''
        Shows the log entry in the bottom pane of the UI
        '''
        self.removeAll()
        if getattr(log_entry, "request", False):
            self.addTab("Request", self._requestViewer.getComponent())
            self._requestViewer.setMessage(log_entry.request, True)
        if getattr(log_entry, "response", False):
            self.addTab("Response", self._responseViewer.getComponent())
            self._responseViewer.setMessage(log_entry.response, False)
        self.addTab("Repo Entry Commands", self.sendPanel)
        self._currentlyDisplayedItem = log_entry
        
    '''
    The three methods below implement IMessageEditorController st. requests 
    and responses are shown in the UI pane
    '''
    def getHttpService(self):
        return self._currentlyDisplayedItem.requestResponse.getHttpService()

    def getRequest(self):
        return self._currentlyDisplayedItem.requestResponse.getRequest()

    def getResponse(self):
        return self._currentlyDisplayedItem.getResponse()

 
class UiTopPane(JTabbedPane):
    '''
    The top pane in this extension's UI tab. It shows either the in-burp 
    version of the Log or an "Options" tab (name TBD).
    '''
    def __init__(self, callbacks, bottom_pane, log):
        self.logTable = UiLogTable(callbacks, bottom_pane, log.gui_log)
        scrollPane = JScrollPane(self.logTable)
        self.addTab("Repo", scrollPane)
        options = OptionsPanel(log)
        self.addTab("Repo Commands", options)
        callbacks.customizeUiComponent(self)

class UiLogTable(JTable):
    '''
    Table of log entries that are shown in the top pane of the UI when
    the corresponding tab is selected.
    
    Note, as a JTable, this stays synchronized with the underlying
    ArrayList. 
    '''
    def __init__(self, callbacks, bottom_pane, gui_log):
        self.bottom_pane = bottom_pane
        self._callbacks = callbacks
        self.gui_log = gui_log
        self.setModel(gui_log)
        callbacks.customizeUiComponent(self)

    def getSelectedEntries(self):
        for i in self.getSelectedRows():
            yield self.gui_log.get(i)
    
    def changeSelection(self, row, col, toggle, extend):
        '''
        Displays the selected item in the content pane
        '''
    
        JTable.changeSelection(self, row, col, toggle, extend)
        self.bottom_pane.show_log_entry(self.gui_log.get(row))

class OptionsPanel(JPanel):
    def __init__(self, log):
        reloadButton = JButton("Reload UI from git repo")
        reloadButton.addActionListener(ReloadAction(log))
        self.add(reloadButton)

class ReloadAction(ActionListener):
    def __init__(self, log):
        self.log = log

    def actionPerformed(self, event):
        self.log.reload()

class SendPanel(JPanel, ActionListener):
    def __init__(self, callbacks):
        self.callbacks = callbacks
        label = JLabel("Send selected results to respective burp tools:")
        sendButton = JButton("Send")
        sendButton.addActionListener(self)
        self.add(label)
        self.add(sendButton)
        self.log_table = None # to be set by caller

    def actionPerformed(self, actionEvent):
        for entry in self.log_table.getSelectedEntries():
            if entry.tool == "repeater":
                https = (entry.protocol == "https")
                self.callbacks.sendToRepeater(entry.host, int(entry.port), 
                        https, entry.request, entry.timestamp)
            elif entry.tool == "scanner":
                issue = BurpLogScanIssue(entry)
                self.callbacks.addScanIssue(issue)


'''
Burp Interoperability Class Definitions
'''

class BurpLogHttpService(IHttpService):
    def __init__(self, host, port, protocol):
        self._host = host
        self._port = port
        self._protocol = protocol

    def getHost(self):
        return self._host

    def getPort(self):
        return int(self._port)

    def getProtocol(self):
        return self._protocol

class BurpLogHttpRequestResponse(IHttpRequestResponse):
    def __init__(self, entry):
        self.entry = entry

    def getRequest(self):
        return self.entry.request
    def getResponse(self):
        return self.entry.response
    def getHttpService(self):
        return BurpLogHttpService(self.entry.host,
                self.entry.port, self.entry.protocol)


class BurpLogScanIssue(IScanIssue):
    '''
    Passed to addScanItem
    Note that a pythonic solution that dynamically creates method based on 
    LogEntry attributes via functools.partial will not work here as the 
    interface classes supplied by Burp (IScanIssue, etc.) include read-only
    attributes corresponding to strings that would be used by such a solution.
    '''
    def __init__(self, entry):
        self.entry = entry
        self.messages = [BurpLogHttpRequestResponse(m) for m in self.entry.messages]
        self.service = BurpLogHttpService(self.entry.host, self.entry.port, self.entry.protocol)

    def getHttpMessages(self):
        return self.messages
    def getHttpService(self):
        return self.service

    def getConfidence(self):
        return self.entry.confidence
    def getIssueBackground(self):
        return self.entry.issue_background
    def getIssueDetail(self):
        return self.entry.issue_detail
    def getIssueName(self):
        return self.entry.issue_name
    def getIssueType(self):
        return self.entry.issue_type
    def getRemediationDetail(self):
        return self.entry.remediation_detail
    def getSeverity(self):
        return self.entry.severity
    def getUrl(self):
        return URL(self.entry.url)


