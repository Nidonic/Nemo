from nemo.tools.base import BaseTool, CallAssessment, FunctionTool, ToolCollection, ToolContext, safe_execute
from nemo.tools.browser import BrowserTool
from nemo.tools.devices import DEVICE_TOOLS
from nemo.tools.files import FILE_TOOLS
from nemo.tools.hands import hands_tools
from nemo.tools.memory_tools import MEMORY_TOOLS
from nemo.tools.planning import PLANNING_TOOLS
from nemo.tools.python_exec import PythonTool
from nemo.tools.shell import ShellTool
from nemo.tools.web import WebFetchTool, WebSearchTool


def builtin_tools() -> ToolCollection:
    return ToolCollection(
        ShellTool(), PythonTool(), *FILE_TOOLS, WebSearchTool(), WebFetchTool(), BrowserTool(),
        *MEMORY_TOOLS, *PLANNING_TOOLS, *DEVICE_TOOLS, *hands_tools(),
    )


__all__ = ["BaseTool", "CallAssessment", "FunctionTool", "ToolCollection", "ToolContext", "builtin_tools", "safe_execute"]
