import QtQuick
import Quickshell.Io

// Shells out to bin/ai-usage-copilot on refresh() and turns its JSON into
// display-ready properties. A caller never sees that a subprocess or the
// GitHub endpoint is involved at all -- just plan/quota/category fields.
Item {
  id: root
  visible: false

  readonly property string scriptPath: String(Qt.resolvedUrl("bin/ai-usage-copilot")).replace("file://", "")

  property bool available: false
  property string plan: ""
  property string quotaResetDate: ""
  // { chat: {unlimited, remaining, entitlement, creditsUsed}, completions: {...}, premium_interactions: {...} }
  property var categories: ({})

  function refresh() {
    if (!proc.running) proc.running = true
  }

  function parse(raw) {
    try {
      var record = JSON.parse(String(raw || ""))
      if (!record || typeof record !== "object" || !record.categories)
        throw new Error("empty collector output")
      root.plan = String(record.plan || "")
      root.quotaResetDate = String(record.quotaResetDate || "")
      root.categories = record.categories
      root.available = true
    } catch (e) {
      root.available = false
    }
  }

  Process {
    id: proc
    command: [root.scriptPath]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.parse(text)
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) root.available = false
    }
  }
}
