import QtQuick
import Quickshell.Io

// Shells out to bin/ai-usage-copilot on refresh() and turns its JSON into
// display-ready properties. A caller never sees that a subprocess or the
// GitHub endpoint is involved at all -- just plan/quota/recentDays fields.
Item {
  id: root
  visible: false

  readonly property string scriptPath: String(Qt.resolvedUrl("bin/ai-usage-copilot")).replace("file://", "")

  property bool available: false
  property string plan: ""
  property string quotaResetDate: ""
  // [{ date, creditsUsed }], oldest first -- as the collector reports it.
  property var recentDays: []
  // Today's credits-used delta, or -1 when it isn't computable yet (no
  // prior-day sample to diff against). Reported by the collector itself,
  // same as Claude's todayPrompts/todaySessions/todayTotalTokens -- a
  // caller never has to date-compare recentDays to find "today" in it.
  property real todayCreditsUsed: -1

  function refresh() {
    if (!proc.running) proc.running = true
  }

  function parse(raw) {
    try {
      var record = JSON.parse(String(raw || ""))
      if (!record || typeof record !== "object" || !record.recentDays)
        throw new Error("empty collector output")
      root.plan = String(record.plan || "")
      root.quotaResetDate = String(record.quotaResetDate || "")
      root.recentDays = record.recentDays
      root.todayCreditsUsed = Number(record.todayCreditsUsed)
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
